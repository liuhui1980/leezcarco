"""
语音识别 ASR API 客户端
调用远程 API 进行语音转文字，替代本地 Whisper 模型
"""
import logging
import os
import time
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ASR API 配置
ASR_API_URL = "http://192.168.100.62:9000/asr"
MAX_RETRY = 3
RETRY_DELAY = 1.0  # 重试间隔（秒）


class TranscribeClient:
    """语音识别 API 客户端"""

    def __init__(self, api_url: str = ASR_API_URL):
        """
        :param api_url: ASR API 地址
        """
        self.api_url = api_url

    def transcribe(self, wav_path: str) -> Dict[str, Any]:
        """
        调用远程 ASR API 进行语音识别，使用 multipart/form-data 上传文件

        :param wav_path: 本地 WAV 音频文件路径
        :return: 包含识别结果的字典：
                 {"text": str, "duration": float}
                 失败时返回 {"text": "", "duration": 0}
        """
        import requests

        if not os.path.exists(wav_path):
            logger.error(f"音频文件不存在：{wav_path}")
            return {"text": "", "duration": 0}

        last_error = None

        for attempt in range(MAX_RETRY):
            try:
                # 使用 multipart/form-data 上传文件
                with open(wav_path, "rb") as f:
                    files = {"audio_file": f}
                    response = requests.post(
                        self.api_url,
                        files=files,
                        timeout=30
                    )

                # 解析响应（直接返回文本字符串，不是 JSON）
                if response.status_code == 200:
                    text = response.text.strip()
                    logger.debug(f"ASR 识别成功：{text[:50] if text else '(空)'}...")
                    return {"text": text, "duration": 0}
                else:
                    # HTTP 错误
                    err_msg = f"HTTP {response.status_code}: {response.text[:100]}"
                    logger.warning(f"ASR API 返回错误 (尝试 {attempt + 1}/{MAX_RETRY}): {err_msg}")
                    last_error = err_msg

            except requests.exceptions.RequestException as e:
                # 网络异常
                last_error = str(e)
                logger.warning(f"ASR API 请求异常 (尝试 {attempt + 1}/{MAX_RETRY}): {e}")

            except Exception as e:
                # 其他异常
                last_error = str(e)
                logger.warning(f"ASR API 调用异常 (尝试 {attempt + 1}/{MAX_RETRY}): {e}")

            # 重试前等待
            if attempt < MAX_RETRY - 1:
                time.sleep(RETRY_DELAY)

        # 所有重试均失败
        logger.error(f"ASR API 调用失败，已重试 {MAX_RETRY} 次：{last_error}")
        return {"text": "", "duration": 0}

    def close(self):
        """关闭客户端资源"""
        pass


# 全局单例（懒加载）
_asr_client: Optional[TranscribeClient] = None
_asr_lock = threading.Lock()


def get_asr_client() -> TranscribeClient:
    """获取全局 ASR 客户端单例"""
    global _asr_client
    with _asr_lock:
        if _asr_client is None:
            _asr_client = TranscribeClient()
            logger.info("ASR API 客户端已初始化")
        return _asr_client
