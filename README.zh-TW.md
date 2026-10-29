# Song of Theseus

[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

**一套以本機為優先的歌聲與歌詞編輯工作台。** Song of Theseus 將逐句改詞、MIDI 填詞、歌手資料管理和整曲生成整合在瀏覽器介面中，推理工作在本機執行。

歌聲生成由 [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus) 提供。本儲存庫包含工作台及其串接程式碼，**不包含模型權重**；權重需另行從上游下載。本專案僅支援推理，不提供訓練功能。

## 功能

- 上傳歌曲後使用 Demucs `htdemucs` 分離人聲與伴奏；可上傳 LRC，也可用本機 Whisper large-v3 產生可編輯的歌詞草稿。
- 逐句修改歌詞、調整句子時間點、試聽原唱並產生替換片段預覽。
- 合成整曲時保留未修改的人聲和原伴奏。
- 匯入 MIDI，按樂句填詞並試聽旋律；可管理歌手資料，並讓同一首作品的各片段共用同一份歌手參考音訊。
- 在本機保存工作區、歌詞草稿和工作歷史。

自動辨識只會產生初稿。演唱、和聲、伴奏和錄音雜訊可能造成錯詞或錯誤時間點，請在生成前檢查歌詞與時間。

## 快速開始

本專案以 Windows 本機使用為主。完整環境設定和疑難排解請見 [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md)。

1. 複製本儲存庫，並將官方 Plus 專案放到本儲存庫旁邊的 `../YingMusic-Singer-Plus`：

   ```powershell
   git clone https://github.com/YOUR_ORGANIZATION/song-of-theseus.git
   git clone https://github.com/ASLP-lab/YingMusic-Singer-Plus.git ..\YingMusic-Singer-Plus
   ```

2. 依照 [LOCAL_WINDOWS.md](LOCAL_WINDOWS.md) 和 Plus 上游說明準備 Python 環境、NVIDIA PyTorch 及模型權重。首次執行時會下載所需權重。
3. 雙擊 `run_local.bat`，然後前往 [http://127.0.0.1:23253](http://127.0.0.1:23253)。

服務會在連接埠 `23253` 監聽所有網路介面，區域網路中的其他裝置也能存取。程式沒有內建登入功能，請只在可信任的私人網路中使用，並適當設定防火牆。

### 模型權重下載

- YingMusic-Singer-Plus：首次載入時從官方模型儲存庫下載。
- Whisper large-v3：首次使用本機歌詞辨識時下載。
- Demucs `htdemucs`：首次使用人聲分離時下載。

模型權重較大。推理速度取決於顯示卡、顯示記憶體、音訊長度和取樣步數。工作會排入佇列，避免多個工作同時佔用顯示記憶體。

## 致謝

感謝以下模型與工具的作者及維護者：

- [YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus)：由 Chunbo Hao、Junjie Zheng、Guobin Ma、Yuepeng Jiang、Huakang Chen、Wenjie Tian、Gongyu Chen、Zihao Chen 和 Lei Xie 等作者開發，為本專案提供改詞演唱與旋律保持能力。
- [Demucs](https://github.com/facebookresearch/demucs)：用於 `htdemucs` 人聲與伴奏分離。
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) 和 [Whisper large-v3](https://huggingface.co/openai/whisper-large-v3)：用於本機歌詞辨識。
- YingMusic-Singer-Plus 上游另致謝 [F5-TTS](https://github.com/SWivid/F5-TTS)、[Stable Audio](https://github.com/Stability-AI/stable-audio-tools)、[SOME](https://github.com/openvpi/SOME)、[DiffRhythm](https://github.com/ASLP-lab/DiffRhythm)、[GTSinger](https://github.com/GTSinger/GTSinger) 和 [Emilia](https://github.com/open-mmlab/Emilia)。

授權範圍和第三方歸屬說明請見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本專案為獨立專案，不代表或獲得上游作者背書。

### 研究引用

如在研究中使用歌聲模型，請引用上游論文：

```bibtex
@article{hao2026yingmusicsingerplus,
  title={YingMusic-Singer: Controllable Singing Voice Synthesis with Flexible Lyric Manipulation and Annotation-free Melody Guidance},
  author={Hao, Chunbo and Zheng, Junjie and Ma, Guobin and Jiang, Yuepeng and Chen, Huakang and Tian, Wenjie and Chen, Gongyu and Chen, Zihao and Xie, Lei},
  journal={arXiv preprint arXiv:2603.24589},
  year={2026}
}
```

## 授權條款

本儲存庫中 Song of Theseus 原創應用程式碼採用 **GNU AGPLv3-only**，詳見 [LICENSE](LICENSE)。此授權不會重新授權需另行下載的模型程式碼或權重；相關上游授權與限制仍然有效。重新散布整合套件或用於商業用途前，請先閱讀 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。介面會顯示 Plus VAE 上游授權要求的 Stability AI 署名，並隨附對應 NOTICE 與授權文字。
