# transcritor-local

**100% local** audio, video and meeting transcription — no API, no cost — using Whisper (`faster-whisper`) on your own PC. Simple web interface (Brazilian Portuguese UI).

> 🚧 Work in progress. Versão em português: [README.md](README.md)

Full documentation (features, requirements, install) will be added in Phase 4.

## Responsible use

- **Personal use.** This program is meant for personal use, for example transcribing your own lectures, meetings and
  videos you need notes from. Do not use it to copy, republish or redistribute other people's content.
- **YouTube.** Respect the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the copyright of whoever
  published the video. Fast mode reads the captions YouTube itself shows; Precise mode downloads the audio only to
  transcribe it and, by default, **deletes the file when done**. Do not download or transcribe videos where the
  platform's terms or your country's law forbid it.
- **Recordings and meetings.** Tell people and get their consent before recording a conversation or meeting.
- **Tests.** The automated tests use no third-party videos: they use synthetic audio and simulations. The only test that
  needs the internet (`pytest -m rede`, optional) uses a NASA video, generally public-domain material.
- **Example video.** The video shown in this project's examples and screenshots is from **NASA Goddard** (public
  domain, unless otherwise noted).

