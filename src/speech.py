"""
主播语音转文字模块
流程：TikTok HLS 流 → ffmpeg 切片 (6 秒 WAV) → 远程 ASR API 转文字 + 语言/方言识别 → 回调
"""
import asyncio
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from src.asr_api import get_asr_client
from src.lang_detect import detect_speech_language, speech_lang_stats

logger = logging.getLogger(__name__)


class SpeechMonitor:
    """
    针对单个直播账号的语音监控
    - 从直播 HLS/FLV 流中持续拉取音频
    - 每 SEGMENT_SECS 秒切一段，送入 ASR API 转文字
    - 转写结果通过 on_transcript 回调传出
    """

    SEGMENT_SECS = 3          # 缩短切片间隔到 3 秒，确保每句话都被采集
    OVERLAP_SECS = 0.5         # 切片重叠 0.5 秒，防止漏掉句尾
    MAX_CONSECUTIVE_ERRORS = 12 # 增大容忍度，避免频繁重连
    RECONNECT_WAIT = 2          # 缩短重连等待时间，更快恢复

    def __init__(self, username: str, stream_url: str, on_transcript: Callable, socketio=None):
        """
        :param username: 主播账号名（用于日志和回调识别）
        :param stream_url: 直播流地址（FLV 或 HLS）
        :param on_transcript: 转写完成的回调函数，参数为 (username, text, timestamp)
        :param socketio: Flask-SocketIO 实例（可选，用于直接推送）
        """
        self.username = username
        self.stream_url = stream_url
        self.on_transcript = on_transcript
        self.socketio = socketio
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._tmpdir: Optional[tempfile.TemporaryDirectory] = None
        self._asr_client = get_asr_client()

    def start(self):
        """启动语音监控线程"""
        if self.running:
            logger.warning(f"[{self.username}] SpeechMonitor 已在运行")
            return
        self.running = True
        self._tmpdir = tempfile.TemporaryDirectory(prefix=f"tiktok_speech_{self.username}_")
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"speech-{self.username}"
        )
        self._thread.start()
        logger.info(f"[{self.username}] 语音监控已启动，流地址：{self.stream_url[:60]}...")

    def stop(self):
        """停止语音监控"""
        self.running = False
        if self._tmpdir:
            try:
                self._tmpdir.cleanup()
            except Exception:
                pass
            self._tmpdir = None
        logger.info(f"[{self.username}] 语音监控已停止")

    def _run_loop(self):
        """主循环：持续拉流切片 → 转文字"""
        seg_idx = 0
        consecutive_errors = 0

        while self.running:
            try:
                # 防止 stop() 在检查 running 后、使用 _tmpdir 前将其置为 None
                tmpdir = self._tmpdir
                if tmpdir is None:
                    break
                wav_path = os.path.join(tmpdir.name, f"seg_{seg_idx:06d}.wav")
                # 使用重叠切片，确保连续采集
                success = self._pull_segment(wav_path, duration=self.SEGMENT_SECS + self.OVERLAP_SECS)

                if not success:
                    consecutive_errors += 1
                    if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        logger.error(f"[{self.username}] 连续 {self.MAX_CONSECUTIVE_ERRORS} 次拉流失败，语音监控停止")
                        break
                    # 指数退避：2s → 4s → 8s → 最大 16s
                    wait = min(self.RECONNECT_WAIT * (2 ** (consecutive_errors - 1)), 16)
                    logger.warning(f"[{self.username}] 拉流失败 ({consecutive_errors}/{self.MAX_CONSECUTIVE_ERRORS})，{wait}s 后重试")
                    time.sleep(wait)
                    continue

                consecutive_errors = 0  # 重置错误计数
                text = self._transcribe(wav_path)

                # 清理临时文件
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

                if text and text.strip():
                    timestamp = time.strftime('%H:%M:%S')

                    # 语言/方言识别
                    lang_info = detect_speech_language({
                        "text": text.strip(),
                        "language": getattr(self, '_last_speech_lang', '')
                    })
                    speech_lang_stats.add(self.username, lang_info)

                    # 详细日志：确保每句话都被记录
                    sentence_length = len(text.strip())
                    word_count = len(text.strip().split())
                    logger.info(f"[{self.username}] [{lang_info['lang_short']}] {timestamp} 采集到 {sentence_length} 字符/{word_count} 词：{text[:100]}")
                    self.on_transcript(self.username, text.strip(), timestamp, lang_info)

                seg_idx += 1

            except Exception as e:
                logger.error(f"[{self.username}] 语音处理异常：{e}")
                consecutive_errors += 1
                time.sleep(2)

    def _pull_segment(self, output_path: str, duration: int) -> bool:
        """
        用 ffmpeg 从直播流中拉取一段音频并保存为 WAV
        - 单声道 16kHz 16bit（ASR API 最佳输入格式）
        - 自动读取 config.py 中的代理配置，TikTok CDN 需要走代理才能访问
        """
        # 读取代理配置（仅取 HTTP 代理，ffmpeg -http_proxy 不支持 socks5）
        http_proxy = None
        try:
            from config import PROXY_HTTP
            if PROXY_HTTP:
                # 将 socks5:// 转换为 http:// 格式（同一端口 Clash 通常同时支持两种协议）
                if PROXY_HTTP.startswith('socks5://'):
                    # 提取 host:port 部分，改用 http 代理（Clash 7897 端口同时支持 HTTP+SOCKS5）
                    host_port = PROXY_HTTP.replace('socks5://', '')
                    http_proxy = f"http://{host_port}"
                elif PROXY_HTTP.startswith('http://'):
                    http_proxy = PROXY_HTTP
        except Exception:
            pass

        cmd = ["ffmpeg", "-loglevel", "error"]

        # 对 HLS/HTTPS 流加上 HTTP 代理（必须在 -i 之前指定）
        if http_proxy:
            cmd += ["-http_proxy", http_proxy]

        cmd += [
            "-i", self.stream_url,
            "-t", str(duration),       # 拉取时长
            "-vn",                      # 不要视频
            "-acodec", "pcm_s16le",     # WAV PCM 16bit
            "-ar", "16000",             # 16kHz
            "-ac", "1",                 # 单声道
            "-y",                       # 覆盖已存在文件
            output_path
        ]
        try:
            logger.debug(f"[{self.username}] ffmpeg 拉流 (proxy={http_proxy}): {self.stream_url[:60]}")
            result = subprocess.run(
                cmd,
                timeout=duration + 20,  # 超时保护（代理有额外延迟，适当延长）
                capture_output=True
            )
            if result.returncode != 0:
                err_msg = result.stderr.decode(errors='replace')[:300]
                logger.debug(f"[{self.username}] ffmpeg 错误：{err_msg}")
                return False
            # 检查文件是否有内容
            exists = os.path.exists(output_path) and os.path.getsize(output_path) > 1000
            if exists:
                logger.debug(f"[{self.username}] 拉流成功，文件大小：{os.path.getsize(output_path)} bytes")
            return exists
        except subprocess.TimeoutExpired:
            logger.warning(f"[{self.username}] ffmpeg 拉流超时（超过 {duration+20}s）")
            return False
        except Exception as e:
            logger.error(f"[{self.username}] ffmpeg 异常：{e}")
            return False

    def _transcribe(self, wav_path: str) -> str:
        """
        调用远程 ASR API 对 WAV 文件转文字
        自动检测语言（中/英/阿拉伯语等）
        """
        try:
            result = self._asr_client.transcribe(wav_path)
            text = result.get("text", "").strip()

            if not text:
                return ""

            # 过滤疑似幻觉输出（常见的无意义重复）
            if len(set(text.split())) <= 2 and len(text) > 20:
                logger.debug(f"[{self.username}] 过滤疑似幻觉输出：{text[:40]}")
                return ""

            return text
        except Exception as e:
            logger.error(f"[{self.username}] ASR API 转写失败：{e}")
            return ""


async def get_stream_url_from_client(client) -> Optional[str]:
    """
    从 TikTokLiveClient 中提取直播流地址
    需要在 ConnectEvent 之后调用（此时 room_id 已确定）
    多重备用策略，大幅提升成功率
    """
    import json

    # ── 方案 A：从 room_info 获取流地址（TikTokLive 官方路径）──
    try:
        room_info = await client.web.fetch_room_info()
        # room_info 返回的就是 data 字段内容，stream_url 是顶层键
        stream_url_obj = room_info.get('stream_url', {})
        if not stream_url_obj:
            logger.warning("[方案 A] room_info 中 stream_url 为空")
        else:
            stream_data_raw = (
                stream_url_obj
                .get('live_core_sdk_data', {})
                .get('pull_data', {})
                .get('stream_data', '{}')
            )
            stream_data = json.loads(stream_data_raw) if isinstance(stream_data_raw, str) else stream_data_raw

            # 优先 HLS（更稳定），其次 FLV；质量从低到高按稳定性排序
            for quality in ['sd', 'ld', 'hd', 'uhd', 'origin']:
                quality_data = stream_data.get('data', {}).get(quality, {}).get('main', {})
                hls_url = quality_data.get('hls', '')
                flv_url = quality_data.get('flv', '')
                if hls_url:
                    logger.info(f"[方案 A] 获取到 HLS 流地址 (quality={quality}): {hls_url[:80]}")
                    return hls_url
                if flv_url:
                    logger.info(f"[方案 A] 获取到 FLV 流地址 (quality={quality}): {flv_url[:80]}")
                    return flv_url

            logger.warning(f"[方案 A] stream_data 中未找到流地址，stream_data keys: {list(stream_data.get('data', {}).keys())}")
    except Exception as e:
        logger.warning(f"[方案 A] fetch_room_info 失败：{type(e).__name__}: {e}")

    # ── 方案 B：直接从 client._web.params 获取 room_id 构造流地址 ──
    try:
        room_id = None
        # 尝试多种方式获取 room_id
        if hasattr(client, 'room_id') and client.room_id:
            room_id = str(client.room_id)
        elif hasattr(client, '_room_id') and client._room_id:
            room_id = str(client._room_id)
        elif hasattr(client, 'web') and hasattr(client.web, 'params'):
            params_room_id = client.web.params.get('room_id')
            if params_room_id:
                room_id = str(params_room_id)

        if room_id and room_id.isdigit():
            flv_url = f"https://pull-flv-f26-va01.tiktokcdn.com/stage/stream-{room_id}.flv"
            logger.info(f"[方案 B] 尝试构造 FLV URL: room_id={room_id}")
            return flv_url
        else:
            logger.warning(f"[方案 B] 无法获取有效 room_id（当前值：{room_id}），跳过")
    except Exception as e:
        logger.warning(f"[方案 B] 构造流地址失败：{e}")

    logger.error("所有方案均无法获取直播流地址，语音监控将跳过")
    return None
