#!/usr/bin/env python3
"""Generate localization/umineko.pt-BR.toml from a verified Shin key list.

The keys are copied verbatim from AuroraWright/shin's
``localization/umineko.en.toml`` at the pinned revision.  Only the 26 genuine
UI strings are translated here; the 34 long strings in that file are
Bernkastel's game dialogue mirrored from the scenario and are deliberately
left untranslated until a reviewed pt-BR source is attached.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

# Index (1-based, in the pinned reference file) -> Brazilian Portuguese value.
PT_BR: dict[int, str] = {
    35: "Carregar este marcador?",
    36: "Excluir este marcador?",
    37: "Salvar neste marcador?",
    38: "Salvar neste marcador?@rO conteúdo atual será substituído.",
    39: "Marcador vazio",
    40: "Posição do cursor",
    41: "Posição atual no jogo",
    42: "Posição do cursor / posição no jogo",
    43: "Início",
    44: "Fim",
    45: "Restaurar todas as configurações para os valores padrão?",
    46: "Sair do jogo e voltar à tela de título?@rDeseja continuar?",
    47: "Pular até o próximo capítulo ou escolha?@rDeseja continuar?",
    48: "Voltar ao capítulo anterior?@rDeseja continuar?",
    49: "{}h {}min",
    50: "{minutes} min",
    51: "Sim",
    52: "Não",
    53: "Ir para a linha selecionada?@rDeseja continuar?",
    54: "Salvando dados.@rNão desligue o console nem feche o jogo.",
    55: "Iniciar este cenário?@rDeseja continuar?",
    56: (
        "Todos os oito episódios principais e parte dos episódios extras@r"
        "serão desbloqueados. Você aceita?@r@z75."
        "Após desbloquear, não será possível voltar ao estado original. Tenha cuidado."
    ),
    57: (
        "Durante o modo automático e a reprodução na Music Box,@r"
        "o modo de suspensão automático do console será desativado."
    ),
    58: "OK",
    59: "Conclusão: 100％",
    60: "Conclusão: {}.{}％",
}


def toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, help="Shin umineko.en.toml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    reference = tomllib.loads(Path(args.reference).read_text(encoding="utf-8"))
    keys = list(reference)
    if len(keys) != 60:
        raise SystemExit(f"unexpected reference key count: {len(keys)} (expected 60)")

    lines = [
        "# Brazilian Portuguese UI overrides for Shin's umineko game.",
        "# Keys are copied verbatim from upstream/shin/localization/umineko.en.toml",
        "# at commit 8875fd49f1961265d4101c541027a11db629aa9b (branch fork).",
        "# Install as localized.toml in the assets directory. This affects engine UI only;",
        "# scenario dialogue is translated via mod.rom.",
        "# 34 long Bernkastel-game dialogue keys in the reference list remain untranslated.",
        "",
    ]
    for index, value in sorted(PT_BR.items()):
        key = keys[index - 1]
        lines.append(f"# reference key #{index}")
        lines.append(f"{toml_string(key)} = {toml_string(value)}")
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(PT_BR)} keys to {args.output}")


if __name__ == "__main__":
    main()
