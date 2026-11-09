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

## Estimativa para o requisito mínimo (4 núcleos)

Para estimar um PC mais simples, limitei o Whisper a **4 threads** (`cpu_threads=4`) neste mesmo notebook, com o mesmo áudio:

| Modelo | Threads | Arquivo de 173 s | Velocidade | Trecho ao vivo de 30 s (média / pior) |
|---|---|---|---|---|
| `small` | 4 | 56,7 s | **3,1× tempo real** | 13,2 s / 15,1 s |
| `base` | 4 | 24,4 s | **7,1× tempo real** | 4,4 s / 6,1 s |

**Isto é uma estimativa, não uma medição em um PC de 4 núcleos.** Limitar threads simula a *quantidade* de núcleos, mas não a *velocidade* deles:
os núcleos deste i5 de 13ª geração são rápidos, e uma CPU de 4 núcleos mais antiga ou de entrada provavelmente será mais lenta que estes números
(e a RAM de 8 GB também não foi simulada). Leia como o "melhor caso" do requisito mínimo.

## Leitura dos números

- Em CPU como esta, o `small` transcreve de 3 a 4× mais rápido que o tempo real: uma reunião de 1 hora fica pronta em ~16 a 20 min,
  e a gravação ao vivo acompanha (≈9 a 13 s de processamento por trecho de 30 s).
- O `base` é de 2,3 a 2,7× mais rápido que o `small`, mas erra mais (ver abaixo).

## Ressalvas (para o README)

- Os números usam **fala sintética limpa**; fala real com ruído, sotaques ou várias pessoas pode ser mais lenta e menos precisa.
- Valem para **este notebook** (i5-13420H + RTX 3050), não para PCs mais fracos.
- **Em CPU, modelos pequenos alucinam mais sobre ruído** (detalhes abaixo).

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
