"""Parche de compatibilidad al provider: funcionar con el modelo que haya."""
from pathlib import Path

VIEJO = '''        installed = [m.get("model", "") for m in tags.get("models", [])]
        if self.model not in installed:
            raise ProviderError(
                f"el modelo '{self.model}' no esta instalado. Disponibles: {', '.join(installed) or 'ninguno'}. "
                f"Instalalo con: ollama pull {self.model}"
            )
        return {
            "provider": self.name,
            "host": self._host,
            "version": str(version.get("version", "?")),
            "model": self.model,
            "installed_models": ", ".join(installed),
        }'''

NUEVO = '''        installed = [m.get("model", "") for m in tags.get("models", [])]
        nota = ""
        if self.model not in installed:
            # Si alguien pidio un modelo EXPLICITAMENTE, no se lo cambiamos: pidio
            # ese. Pero si venimos con el default del codigo y no esta instalado,
            # ese default es una opinion sobre OTRA maquina -- buscamos uno que
            # este y que sepa usar tools.
            if os.environ.get("LOCALFORGE_MODEL"):
                raise ProviderError(
                    f"el modelo '{self.model}' no esta instalado. Disponibles: "
                    f"{', '.join(installed) or 'ninguno'}. Instalalo con: ollama pull {self.model}"
                )
            elegido = await self._primero_con_tools(installed)
            if elegido is None:
                raise ProviderError(
                    "ninguno de los modelos instalados soporta tool calling, que es el requisito "
                    f"duro del harness. Instalados: {', '.join(installed) or 'ninguno'}. "
                    "Probá: ollama pull qwen3:8b"
                )
            nota = f"'{self.model}' no esta instalado; se usa '{elegido}'"
            self.model = elegido

        info = {
            "provider": self.name,
            "host": self._host,
            "version": str(version.get("version", "?")),
            "model": self.model,
            "installed_models": ", ".join(installed),
        }
        if nota:
            info["nota"] = nota
        return info

    async def _primero_con_tools(self, instalados: list[str]) -> str | None:
        """El primer modelo instalado que sepa usar tools.

        `/api/show` lista las capabilities de un modelo. Sin `tools` el harness no
        puede funcionar: el modelo nunca va a pedir una herramienta, asi que el
        loop termina en el primer turno sin haber mirado nada.
        """
        for nombre in instalados:
            try:
                data = (
                    await self._client.post(
                        f"{self._host}/api/show", json={"model": nombre}, timeout=10
                    )
                ).json()
            except (httpx.HTTPError, ValueError):
                continue
            if "tools" in (data.get("capabilities") or []):
                return nombre
        return None'''


def aplicar(src: Path) -> bool:
    s = src.read_text(encoding="utf-8")
    if "_primero_con_tools" in s:
        return False
    assert s.count(VIEJO) == 1, f"no encontre el bloque de health en {src}"
    s = s.replace(VIEJO, NUEVO)
    if "\nimport os\n" not in s:
        s = s.replace("import time\nfrom typing import Any", "import os\nimport time\nfrom typing import Any", 1)
    assert "import os" in s, f"no pude agregar import os en {src}"
    src.write_text(s, encoding="utf-8")
    return True


if __name__ == "__main__":
    import sys
    n = 0
    for d in sorted(Path("worlds").iterdir()):
        f = d / "src" / "localforge" / "providers" / "ollama.py"
        if f.is_file() and aplicar(f):
            n += 1
            print(f"  {d.name}: provider parchado")
    print(f"\n{n} providers con autodeteccion de modelo")
