# Inference

`tau.inference` is Tau's standalone inference subsystem. It can be used by the
agent runtime or imported directly by applications that only need model access.
Provider installation and credential setup are documented separately in
[Inference Providers](inference-providers.md).

## Responsibilities

The subsystem owns:

- text, image, audio, and video inference clients
- normalized request, response, and streaming event types
- model and provider metadata and registries
- API adapter selection and lazy loading
- API-key and OAuth credential integration
- option merging, retries, and provider error classification

It does not own conversation orchestration, session persistence, tool
execution, or terminal rendering. Those concerns live in `tau.agent`,
`tau.session`, `tau.engine`, and `tau.tui`.

## Package Layout

| Path | Purpose |
|------|---------|
| `tau/inference/__init__.py` | Public clients and shared inference types |
| `tau/inference/types.py` | Contexts, options, results, events, and stop reasons |
| `tau/inference/api/` | Modality-specific adapters and client services |
| `tau/inference/api/text/` | Streaming text inference adapters |
| `tau/inference/api/image/` | Image generation adapters |
| `tau/inference/api/audio/` | Speech synthesis and transcription adapters |
| `tau/inference/api/video/` | Video generation adapters |
| `tau/inference/model/` | Model descriptors and registries |
| `tau/inference/provider/` | Provider descriptors, registries, and OAuth flows |
| `tau/inference/utils.py` | Error classification and retry helpers |

Built-in model and provider definitions live under `tau.builtins.models` and
`tau.builtins.providers`. The inference registries load those definitions and
may also receive custom entries from extensions or programmatic runtime
configuration.

## Public Clients

The top-level package exposes four clients:

| Client | Operation |
|--------|-----------|
| `LLM` | Stream text, thinking, tool-call, usage, and error events |
| `ImageLLM` | Generate an image from an `ImageContext` |
| `AudioLLM` | Synthesize speech or transcribe audio |
| `VideoLLM` | Generate a video from a `VideoContext` |

Importing these names does not eagerly import every provider SDK. Tau resolves
the selected model, provider, and adapter, then lazily constructs the adapter
when the first request is made.

The concrete modality services expose `list_available()` to obtain models whose
providers have usable authentication in the current environment.

## Examples

These examples use built-in model IDs. Configure the corresponding provider
credential first; see [Inference Providers](inference-providers.md).

### Stream a Text Response

```python
import asyncio

from tau.inference import LLM, LLMContext, LLMOptions, TextDeltaEvent
from tau.message.types import UserMessage


async def main() -> None:
    llm = LLM(
        "gpt-4o",
        options=LLMOptions(temperature=0.2, max_tokens=500),
    )
    context = LLMContext(
        system_prompt="Answer concisely.",
        messages=[UserMessage.from_text("Explain event streaming.")],
    )

    async for event in llm.stream(context):
        if isinstance(event, TextDeltaEvent):
            print(event.text.content, end="", flush=True)


asyncio.run(main())
```

The stream may also contain thinking, tool-call, retry, error, and final usage
events. Check the corresponding event class when the application needs them.

### Generate an Image

```python
import asyncio

from tau.inference import ImageContext, ImageLLM
from tau.message.types import TextContent


async def main() -> None:
    client = ImageLLM("dall-e-3")
    result = await client.generate(
        ImageContext(
            contents=[TextContent(content="A technical cutaway of a lunar rover")],
            size="1024x1024",
            quality="standard",
        )
    )
    print(result.stop_reason, result.output)


asyncio.run(main())
```

`result.output` contains normalized message content. Depending on the adapter,
image content may contain image bytes or a URL.

### Synthesize Speech

```python
import asyncio
from pathlib import Path

from tau.inference import AudioLLM, TTSContext


async def main() -> None:
    client = AudioLLM("tts-1")
    result = await client.synthesize(
        TTSContext(input="Tau inference is available as a library.", voice="alloy")
    )
    Path("speech.mp3").write_bytes(result.audio)


asyncio.run(main())
```

### Transcribe Audio

```python
import asyncio
from pathlib import Path

from tau.inference import AudioFormat, AudioLLM, STTContext


async def main() -> None:
    client = AudioLLM("whisper-1")
    result = await client.transcribe(
        STTContext(
            audio=Path("recording.mp3").read_bytes(),
            format=AudioFormat.MP3,
        )
    )
    print(result.text)


asyncio.run(main())
```

### Generate a Video

```python
import asyncio
from pathlib import Path

from tau.inference import VideoContext, VideoLLM


async def main() -> None:
    client = VideoLLM("fal-ai/veo3-fast")
    result = await client.generate(
        VideoContext(
            prompt="A slow orbital shot around a satellite",
            duration=5,
            aspect_ratio="16:9",
        )
    )
    if result.video is not None:
        Path(f"video.{result.format.value}").write_bytes(result.video)
    else:
        print(result.url)


asyncio.run(main())
```

### List Authenticated Models

```python
from tau.inference.api.audio.service import AudioLLM
from tau.inference.api.image.service import ImageLLM
from tau.inference.api.text.service import TextLLM
from tau.inference.api.video.service import VideoLLM

for client in (TextLLM, ImageLLM, AudioLLM, VideoLLM):
    models = client.list_available()
    print(client.__name__, [model.id for model in models])
```

## Resolution Flow

For each request, the client:

1. resolves the requested model from the modality's model registry
2. resolves a usable provider, including OAuth credential checks
3. selects the model-specific or provider-default API adapter
4. merges provider defaults, model overrides, and request options
5. obtains credentials through `AuthManager`
6. invokes the adapter and returns normalized results or events

Text inference emits `LLMEvent` variants so the agent and direct consumers do
not need provider-specific streaming code. Image, audio, and video clients
return normalized result dataclasses.

## Extension Boundary

Extensions can register models, providers, and API adapters through the
extension API. Applications that need complete dependency control can provide
custom model, provider, API, and authentication registries to the client
constructors.

See [Extensions](extensions.md) for registration APIs and
[Python API](python-api.md) for embedding the full Tau runtime.
