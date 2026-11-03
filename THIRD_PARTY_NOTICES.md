# Third-party notices

Song of Theseus is an independently maintained application. The source code in this repository is not the same work as the separately downloaded models and dependencies listed below. This project does not bundle or relicense their source code or model weights.

## Original YingMusic-Singer repository

The [GiantAILab/YingMusic-Singer](https://github.com/GiantAILab/YingMusic-Singer) README identifies that upstream project as MIT-licensed. This Song of Theseus release does not include its `src/singer` implementation, checkpoints, or sample assets. The current singing backend is the separately checked-out YingMusic-Singer-Plus project described below. If you separately obtain or redistribute files from the original repository, follow the stated MIT terms and include the required copyright and permission notice; consult the upstream maintainers because its current Git tree has no standalone `LICENSE` file.

## Singing synthesis: YingMusic-Singer-Plus

The Windows setup loads the official [ASLP-lab/YingMusic-Singer-Plus](https://github.com/ASLP-lab/YingMusic-Singer-Plus) checkout from a sibling directory. The Docker image includes the inference source pinned to [commit `baa409c`](https://github.com/ASLP-lab/YingMusic-Singer-Plus/commit/baa409c2e7e5e775f09b4e92a220808f4827d2cc). Its repository states that its code and model weights are under [**CC BY 4.0**](https://creativecommons.org/licenses/by/4.0/), except the VAE model weights and inference code derived from Stable Audio Open, which are under the **Stability AI Community License**. See the [upstream license statement](https://github.com/ASLP-lab/YingMusic-Singer-Plus#-license) and [Stability AI license](https://stability.ai/license). The required Stability attribution is in [NOTICE](NOTICE), the license text is included at [LICENSES/STABILITY_AI_COMMUNITY_LICENSE.txt](LICENSES/STABILITY_AI_COMMUNITY_LICENSE.txt), and the UI displays “Powered by Stability AI.” The model checkpoint is downloaded separately during setup and is not included in the Docker image.

The Plus project acknowledges work from [F5-TTS](https://github.com/SWivid/F5-TTS), [Stable Audio](https://github.com/Stability-AI/stable-audio-tools), [SOME](https://github.com/openvpi/SOME), [DiffRhythm](https://github.com/ASLP-lab/DiffRhythm), [GTSinger](https://github.com/GTSinger/GTSinger), and [Emilia](https://github.com/open-mmlab/Emilia). Consult those upstream projects for their own licenses and conditions.

## Other inference components

- [Demucs](https://github.com/facebookresearch/demucs), using the `htdemucs` checkpoint, separates vocals and accompaniment.
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) runs the [Whisper large-v3 checkpoint](https://huggingface.co/openai/whisper-large-v3) for local lyric transcription.

These are installed or downloaded separately; their own license notices apply. The model checkpoints are not included in this repository.

## AGPL scope

The [GNU AGPLv3-only](LICENSE) license applies to the original Song of Theseus application source and documentation in this repository. Separately downloaded YingMusic-Singer-Plus files, model weights, and third-party packages retain their upstream licenses.

The Windows setup imports YingMusic-Singer-Plus from an external sibling checkout; the Docker image includes its pinned inference source. Model weights are downloaded separately and are not included in the repository or image. Sources and license texts for included and downloaded components are listed above.
