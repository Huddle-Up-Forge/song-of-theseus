# Song of Theseus

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

**一款本地优先的歌声与歌词编辑工作台。** Song of Theseus 将逐句改词、MIDI 填词、歌手资料管理和整曲生成整合在浏览器界面中，推理任务在本机运行。

歌声生成由 [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus) 提供。本仓库包含工作台及其接入代码，**不包含模型权重**；权重需要从上游另行下载。本项目只支持推理，不提供训练功能。

## 功能

- 上传歌曲后使用 Demucs `htdemucs` 分离人声与伴奏；可上传 LRC，也可用本机 Whisper large-v3 生成可编辑的歌词草稿。
- 逐句修改歌词、调整句子时间点、试听原唱并生成替换片段预览。
- 合成整曲时保留未改动的人声和原伴奏。
- 导入 MIDI，按乐句填词并试听旋律；可管理歌手资料，并让同一首作品的各片段共用一份歌手参考音频。
- 在本机保存工作区、歌词草稿和任务历史。

自动识别只会生成初稿。演唱、和声、伴奏和录音噪声可能造成错词或错时，请在生成前检查歌词和时间点。

## 快速开始

本项目以 Windows 本地使用为主。完整环境配置和故障排查见 [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md)。

1. 克隆本仓库，并将官方 Plus 项目放到本仓库旁边的 `../YingMusic-Singer-Plus`：

   ```powershell
   git clone https://github.com/Huddle-Up-Forge/song-of-theseus.git
   git clone https://github.com/ASLP-lab/YingMusic-Singer-Plus.git ..\YingMusic-Singer-Plus
   ```

2. 按 [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md) 和 Plus 上游说明准备 Python 环境、NVIDIA PyTorch 及模型权重。首次运行会下载所需权重。
3. 双击 `run_local.bat`，然后访问 [http://127.0.0.1:23253](http://127.0.0.1:23253)。

服务会在端口 `23253` 监听所有网络接口，局域网其他设备也可访问。程序没有内置登录功能，请只在可信的私人网络中使用，并相应配置防火墙。

### 模型权重下载

- YingMusic-Singer-Plus：首次加载时从官方模型仓库下载。
- Whisper large-v3：首次使用本机歌词识别时下载。
- Demucs `htdemucs`：首次使用人声分离时下载。

模型权重较大。推理速度取决于显卡、显存、音频长度和采样步数。任务会进入队列，避免多个任务同时挤占显存。

## 致谢

感谢以下模型和工具的作者及维护者：

- [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus)：由 Chunbo Hao、Junjie Zheng、Guobin Ma、Yuepeng Jiang、Huakang Chen、Wenjie Tian、Gongyu Chen、Zihao Chen 和 Lei Xie 等作者开发，为本项目提供改词演唱与旋律保持能力。
- [Demucs](https://github.com/facebookresearch/demucs)：用于 `htdemucs` 人声与伴奏分离。
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) 和 [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3)：用于本机歌词识别。
- YingMusic-Singer-Plus 上游还致谢 [F5-TTS](https://github.com/SWivid/F5-TTS)、[Stable Audio](https://github.com/Stability-AI/stable-audio-tools)、[SOME](https://github.com/openvpi/SOME)、[DiffRhythm](https://github.com/ASLP-lab/DiffRhythm)、[GTSinger](https://github.com/GTSinger/GTSinger) 和 [Emilia](https://github.com/open-mmlab/Emilia)。

许可证范围和第三方归属说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本项目为独立项目，不代表或获得上游作者背书。

### 研究引用

如在研究中使用歌声模型，请引用上游论文：

```bibtex
@article{hao2026yingmusicsingerplus,
  title={YingMusic-Singer: Controllable Singing Voice Synthesis with Flexible Lyric Manipulation and Annotation-free Melody Guidance},
  author={Hao, Chunbo and Zheng, Junjie and Ma, Guobin and Jiang, Yuepeng and Chen, Huakang and Tian, Wenjie and Chen, Gongyu and Chen, Zihao and Xie, Lei},
  journal={arXiv preprint arXiv:2603.24589},
  year={2026}
}
```

## 许可证

本仓库中 Song of Theseus 原创应用代码采用 **GNU AGPLv3-only**，见 [LICENSE](LICENSE)。该许可证不会重新许可需单独下载的模型代码或权重；相关上游许可和限制仍然有效。重新分发整合包或用于商业用途前，请先阅读 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。界面显示 Plus VAE 上游许可证要求的 Stability AI 署名，并随附对应 NOTICE 和许可证文本。
