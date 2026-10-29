# Song of Theseus

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

**A local-first singing and lyric editing workstation.** Song of Theseus combines line-by-line lyric rewriting, MIDI lyric filling, singer profiles, and full-song rendering in a browser UI that runs on your own computer.

The singing backend is [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus). This repository contains the workstation and its integration; **model checkpoints are downloaded separately and are not included**. Inference only: this project does not provide training tools.

## What it does

- Upload a song, separate vocals and accompaniment with Demucs `htdemucs`, and supply an LRC file or make an editable lyric draft with local Whisper large-v3 transcription.
- Edit lyrics line by line, adjust line timing, compare the source vocal, and generate a replacement preview.
- Render a complete song while keeping unchanged vocals and the original accompaniment.
- Fill MIDI phrases with lyrics, audition the MIDI melody, manage singer profiles, and use one singer reference across the generated phrases.
- Keep workspaces, lyric drafts, and job history on the local machine.

Automatic transcription is a draft: singing, backing vocals, and noisy mixes can cause recognition errors. Review the text and timing before generation.

## Quick start

This is a Windows-oriented local application. Read [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md) for the complete environment setup and troubleshooting notes.

1. Clone this repository and place the official Plus checkout beside it at `../YingMusic-Singer-Plus`:

   ```powershell
   git clone https://github.com/YOUR_ORGANIZATION/song-of-theseus.git
   git clone https://github.com/ASLP-lab/YingMusic-Singer-Plus.git ..\YingMusic-Singer-Plus
   ```

2. Set up the Python environments and NVIDIA PyTorch dependencies as described in [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md) and the Plus repository. The first run downloads the required checkpoints.
3. Double-click `run_local.bat`, then open [http://127.0.0.1:23253](http://127.0.0.1:23253).

The server listens on all network adapters at port `23253` so other devices on your LAN can connect. It has no built-in login; use it on a trusted private network and configure your firewall accordingly.

### Model downloads

- YingMusic-Singer-Plus: downloaded from its official model repository on first load.
- Whisper large-v3: downloaded when local lyric recognition is first used.
- Demucs `htdemucs`: downloaded when vocal separation is first used.

The checkpoints are large and generation speed depends on the GPU, VRAM, audio length, and sampling steps. The interface queues inference jobs to avoid concurrent GPU memory spikes.

## Acknowledgements

Thank you to the authors and maintainers of the models and tools that make this workstation possible:

- [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus) by Chunbo Hao, Junjie Zheng, Guobin Ma, Yuepeng Jiang, Huakang Chen, Wenjie Tian, Gongyu Chen, Zihao Chen, and Lei Xie — lyric-controlled singing synthesis and melody-preserving editing.
- [Demucs](https://github.com/facebookresearch/demucs) — vocal and accompaniment separation (`htdemucs`).
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) and [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3) — local lyric transcription.
- YingMusic-Singer-Plus also credits [F5-TTS](https://github.com/SWivid/F5-TTS), [Stable Audio](https://github.com/Stability-AI/stable-audio-tools), [SOME](https://github.com/openvpi/SOME), [DiffRhythm](https://github.com/ASLP-lab/DiffRhythm), [GTSinger](https://github.com/GTSinger/GTSinger), and [Emilia](https://github.com/open-mmlab/Emilia).

Please see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for license scope and attribution details. This project is independent and is not endorsed by the upstream authors.

### Research citation

If you use the singing model in research, please cite the upstream work:

```bibtex
@article{hao2026yingmusicsingerplus,
  title={YingMusic-Singer: Controllable Singing Voice Synthesis with Flexible Lyric Manipulation and Annotation-free Melody Guidance},
  author={Hao, Chunbo and Zheng, Junjie and Ma, Guobin and Jiang, Yuepeng and Chen, Huakang and Tian, Wenjie and Chen, Gongyu and Chen, Zihao and Xie, Lei},
  journal={arXiv preprint arXiv:2603.24589},
  year={2026}
}
```

## License

The original Song of Theseus application code in this repository is licensed under **GNU AGPLv3 only**; see [LICENSE](LICENSE). This license does not relicense separately downloaded model code or weights. Their upstream licenses and restrictions remain in effect; review [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing a bundle or using it commercially. The app displays the Stability AI attribution required by the Plus VAE's upstream license; the accompanying `NOTICE` and license copy are included for that component.
