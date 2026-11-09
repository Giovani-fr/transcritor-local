# Medições de velocidade

Medido em 2026-11-09 neste notebook (o mesmo usado no desenvolvimento):

- CPU: Intel Core i5-13420H (8 núcleos, 12 threads), 15,7 GB de RAM
- GPU: NVIDIA GeForce RTX 3050 Laptop (4 GB)
- `faster-whisper` 1.2.1, `compute_type` `int8` (CPU) e `int8_float16` (GPU), VAD ligado

**Áudio de teste:** 173 s de fala sintética em português (voz do Windows), limpa, sem ruído de fundo.
Fala real com ruído pode ser um pouco mais lenta e menos precisa.

| Modelo | Dispositivo | Carregar modelo | Arquivo de 173 s | Velocidade | Trecho ao vivo de 30 s (média / pior) |
|---|---|---|---|---|---|
| `base` | CPU | 1,4 s | 17,0 s | **10,2× tempo real** | 3,7 s / 4,1 s |
| `small` | CPU | 2,2 s | 45,7 s | **3,8× tempo real** | 8,9 s / 10,4 s |
| `large-v3-turbo` | GPU | 8,9 s | 6,5 s | **26,4× tempo real** | 1,2 s / 1,6 s |

"N× tempo real" = quantos segundos de áudio são transcritos por segundo de processamento.
Na gravação ao vivo, enquanto cada trecho de 30 s demorar bem menos que 30 s para ser processado, o texto não acumula atraso.

## Leitura dos números

- Em CPU como esta (8 núcleos), o modelo `small` transcreve ~4× mais rápido que o tempo real: uma reunião de 1 hora fica pronta em cerca de 16 min,
  e a gravação ao vivo acompanha com folga (≈9 s de processamento por trecho de 30 s).
- O `base` é ~2,7× mais rápido que o `small`, mas erra mais (ver limitações abaixo).
- **Não medido:** CPU de 4 núcleos / 8 GB de RAM (requisito mínimo declarado). Este notebook tem o dobro de núcleos;
  em uma CPU mais fraca os tempos serão maiores. Os números acima valem para esta máquina, não para o requisito mínimo.

## Limitações observadas nos testes com ruído sintético

Em 30 s de ruído branco alto (RMS 0,015, depois normalizado pelo app) com a mesma semente aleatória:

| Modelo | Resultado |
|---|---|
| `base` (CPU) | inventou "E aí!" |
| `small` (CPU) | inventou "Legendas por Paulo Montenegro" (agora filtrado) |
| `large-v3-turbo` (GPU) | nada (correto) |

Modelos menores alucinam mais sobre ruído. O filtro descarta as alucinações conhecidas (inclusive créditos de legenda),
mas frases curtas e plausíveis como "E aí!" podem passar no `base`. Por isso o app usa `small` por padrão em CPU
e só recua para `base` em PCs com pouca RAM (< 6 GB).
