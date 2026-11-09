# transcritor-local

Transcrição de áudio, vídeo e reuniões **100% local**, sem API e sem custo, usando Whisper (`faster-whisper`) no seu próprio PC. Interface web simples, em português.

> 🚧 Em construção. English version: [README.en.md](README.en.md)

Documentação completa (funções, requisitos, instalação) será adicionada na Fase 4.

## Aviso de uso responsável

- **Uso pessoal.** Este programa é para uso pessoal, por exemplo transcrever suas próprias aulas, reuniões e vídeos
  de que você precisa fazer anotações. Não o use para copiar, republicar ou redistribuir conteúdo de terceiros.
- **YouTube.** Respeite os [Termos de Serviço do YouTube](https://www.youtube.com/t/terms) e os direitos autorais de quem
  publicou o vídeo. O modo Rápido lê as legendas que o próprio YouTube mostra; o modo Preciso baixa o áudio só para
  transcrever e, por padrão, **apaga o arquivo ao terminar**. Não baixe nem transcreva vídeos quando isso for proibido
  pelos termos da plataforma ou pela lei do seu país.
- **Gravações e reuniões.** Avise e peça o consentimento das pessoas antes de gravar uma conversa ou reunião.
- **Testes.** Os testes automáticos não usam vídeos de terceiros: usam áudio sintético e simulações. O único teste com
  internet (`pytest -m rede`, opcional) usa um vídeo da NASA, material em geral de domínio público.

