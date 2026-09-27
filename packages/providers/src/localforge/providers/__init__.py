from localforge.providers.base import ModelProvider, ProviderError
from localforge.providers.ollama import OllamaProvider

__all__ = ["ModelProvider", "ProviderError", "OllamaProvider", "build_provider"]


def build_provider(cfg=None):
    """Fabrica del provider segun configuracion.

    El harness nunca llama a esto: recibe un ModelProvider ya construido.
    Este es el unico lugar del codigo que mapea nombre -> implementacion.
    """
    from localforge.config import settings as default_settings

    cfg = cfg or default_settings
    if cfg.provider == "ollama":
        return OllamaProvider(cfg)
    raise ValueError(
        f"provider desconocido: '{cfg.provider}'. Implementados: ollama. "
        "Proximos: llamacpp, openai, anthropic."
    )
