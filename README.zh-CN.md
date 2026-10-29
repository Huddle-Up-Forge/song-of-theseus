<a id="readme-top"></a>

<div align="center">
  <a href="https://github.com/Huddle-Up-Forge/song-of-theseus">
    <img src="./docs/assets/song-of-theseus-banner.jpg" alt="Song of Theseus — One of ILLUSIA's Creations" width="100%" />
  </a>
  <h1>Song of Theseus</h1>
  <p><strong>让每一句，重新开口。</strong></p>
  <p>本地优先的歌声与歌词编辑工作台。</p>
  <p>
    <img src="https://img.shields.io/badge/license-AGPL--3.0--only-7467ef?style=flat-square" alt="AGPL-3.0-only" />
    <img src="https://img.shields.io/badge/inference-local-48a9a6?style=flat-square" alt="本机推理" />
    <img src="https://img.shields.io/badge/平台-Windows-5271d8?style=flat-square" alt="Windows" />
  </p>
  <p>
    <a href="./README.md">English</a> · <a href="./README.zh-CN.md">简体中文</a> · <a href="./README.zh-TW.md">繁體中文</a>
  </p>
  <p>
    <a href="#overview">项目介绍</a> · <a href="#features">功能</a> · <a href="#quick-start">快速开始</a> · <a href="#documentation">文档</a> · <a href="#credits">致谢与许可</a>
  </p>
</div>

<a id="overview"></a>

## 项目介绍

Song of Theseus 是一款本地优先的歌声与歌词编辑工作台。上传歌曲后，可以逐句修改歌词、调整句子时间点、用 MIDI 旋律辅助填词、试听替换片段，并在本机拼接整曲。

歌声生成由 [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus) 提供。本仓库包含网页工作台及其接入代码；模型代码和权重需要另行下载。本项目只提供推理，不包含训练工具。

<a id="features"></a>

## 功能

| 工作流程 | 功能说明 |
| --- | --- |
| 🎙️ 逐句改唱 | 分离人声和伴奏，逐句修改歌词、调整时间点，并预览重唱片段。 |
| 🎼 MIDI 填词 | 导入 MIDI、试听旋律、按乐句填写歌词，并让生成片段共用同一份歌手参考音频。 |
| ✍️ 歌词识别 | 使用本机 Whisper large-v3 生成带时间戳的可编辑草稿，也可直接上传 LRC。自动识别可能误听演唱，请检查歌词和时间点。 |
| 🎧 整曲生成 | 保留未改动的人声和原伴奏，只替换修改过的歌词片段并拼接输出。 |
| 💾 本地工作区 | 在本机保存项目、歌词草稿、歌手资料和任务历史。 |

<a id="quick-start"></a>

## 快速开始

本项目以 Windows 本地运行为主。首次启动前请先按完整指南配置环境：

1. 克隆本仓库，并将官方 Plus 项目放到旁边：

   ```powershell
   git clone https://github.com/Huddle-Up-Forge/song-of-theseus.git
   git clone https://github.com/ASLP-lab/YingMusic-Singer-Plus.git ..\YingMusic-Singer-Plus
   ```

2. 按 [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md) 和 Plus 上游说明配置 Python 环境、CUDA 版 PyTorch 及模型权重。
3. 运行 `run_local.bat`，然后访问 [http://127.0.0.1:23253](http://127.0.0.1:23253)。

服务会监听所有网络接口，局域网设备也能访问。程序没有内置登录，请只在可信的私人网络中使用，并配置好防火墙。

<a id="documentation"></a>

## 文档

- [Windows 环境配置与故障排查](LOCAL_WINDOWS.md)
- [第三方声明与许可证范围](THIRD_PARTY_NOTICES.md)
- [GNU AGPLv3-only 许可证](LICENSE)

YingMusic-Singer-Plus、Whisper large-v3 和 Demucs 权重会在首次使用对应功能时另行下载。本仓库不包含模型权重或生成音频。生成速度取决于显卡、可用显存、音频长度和采样步数。

<a id="credits"></a>

## 致谢与许可

感谢以下模型和工具的作者及维护者：

- [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus)，作者 Chunbo Hao、Junjie Zheng、Guobin Ma、Yuepeng Jiang、Huakang Chen、Wenjie Tian、Gongyu Chen、Zihao Chen 和 Lei Xie，为本项目提供改词演唱与旋律保持能力。
- [Demucs](https://github.com/facebookresearch/demucs)，用于 `htdemucs` 人声与伴奏分离。
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) 和 [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3)，用于本机歌词识别。
- YingMusic-Singer-Plus 上游还致谢 [F5-TTS](https://github.com/SWivid/F5-TTS)、[Stable Audio](https://github.com/Stability-AI/stable-audio-tools)、[SOME](https://github.com/openvpi/SOME)、[DiffRhythm](https://github.com/ASLP-lab/DiffRhythm)、[GTSinger](https://github.com/GTSinger/GTSinger) 和 [Emilia](https://github.com/open-mmlab/Emilia)。

本仓库中的 Song of Theseus 原创应用代码采用 **GNU AGPLv3-only**。这不会重新许可另行下载的模型代码或权重。YingMusic-Singer-Plus 与其基于 Stability AI 的 VAE 适用不同许可；重新分发整合包或用于商业用途前，请先阅读 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

如在研究中使用歌声模型，请引用上游论文：

```bibtex
@article{hao2026yingmusicsingerplus,
  title={YingMusic-Singer: Controllable Singing Voice Synthesis with Flexible Lyric Manipulation and Annotation-free Melody Guidance},
  author={Hao, Chunbo and Zheng, Junjie and Ma, Guobin and Jiang, Yuepeng and Chen, Huakang and Tian, Wenjie and Chen, Gongyu and Chen, Zihao and Xie, Lei},
  journal={arXiv preprint arXiv:2603.24589},
  year={2026}
}
```

<p align="right"><a href="#readme-top">返回顶部 ↑</a></p>
