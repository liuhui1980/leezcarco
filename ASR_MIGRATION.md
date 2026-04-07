# 语音识别 API 改造记录

## 改造概述

将语音识别从本地 Whisper 模型改为调用远程 API，减少本地资源占用和模型下载需求。

## API 接口

- **URL**: `http://192.168.100.62:9000/asr`
- **Method**: POST
- **Content-Type**: multipart/form-data
- **字段名**: `audio_file` (文件上传)
- **成功响应**: 直接返回识别文本字符串（不是 JSON）
- **失败响应**: HTTP 错误状态码 + 错误信息

## curl 案例

```bash
curl --request POST \
  --url http://192.168.100.62:9000/asr \
  --form "audio_file=@/path/to/audio.wav"
```

## 改动清单

### 1. 修改 `src/asr_api.py`

- **移除**:
  - `_AudioFileHandler` 类（临时 HTTP 处理器）
  - `_TempHTTPServer` 类（临时 HTTP 服务器）
  - `_get_audio_url()` 方法
  - `self._temp_server` 相关代码
  
- **修改**:
  - `ASR_API_URL`: 改为 `http://192.168.100.62:9000/asr`
  - `transcribe()`: 改用 `files={"audio_file": f}` 上传文件
  - 响应解析：直接读取 `response.text`（纯文本），不再解析 JSON
  - `close()`: 简化为空实现（不再需要关闭 HTTP 服务器）

### 2. 修改 `src/speech.py`

- 无需要修改（接口签名保持不变）

### 3. 更新 `requirements.txt`

- **移除**: `openai-whisper==20250625`
- **保留**: `requests==2.32.5`（ASR API 调用需要）

## 技术方案

### 文件上传

使用 `requests` 库的 `files` 参数实现 multipart/form-data 上传：

```python
with open(wav_path, "rb") as f:
    files = {"audio_file": f}
    response = requests.post(api_url, files=files, timeout=30)
```

### 重试机制

- 最多重试 3 次
- 每次失败后等待 1 秒
- 记录每次错误日志
- 所有重试失败后返回空结果

## 优势

1. **零模型下载**: 无需下载 3GB 的 Whisper large-v3 模型
2. **降低资源占用**: 不需要 GPU 或高性能 CPU 进行推理
3. **简化部署**: 减少依赖项，`pip install` 更快
4. **无临时服务**: 不再需要启动临时 HTTP 服务器
5. **保持兼容**: 接口签名不变，上层调用无需修改

## 注意事项

1. **网络依赖**: 需要能访问 `http://192.168.100.62:9000/asr`
2. **内网 API**: 该 API 为内网地址，仅在局域网内可访问
3. **文件格式**: 上传的文件必须为 WAV 格式
