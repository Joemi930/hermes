"""La porte de validation.

Aucun rendu de worker n'est accepté sur parole. Le lead exécute cette porte
lui-même sur le worktree, et n'accepte que si tout passe.

Un contrôle marqué `non_negociable` ne peut jamais être ignoré, même si
l'appelant le demande : ce sont les points bloquants du projet.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Un contrôle qui dépasse ça est considéré en échec : mieux vaut un verdict
# tranché qu'un lead qui attend indéfiniment un test parti en boucle.
DELAI_MAX_S = 900


@dataclass
class Resultat:
    nom: str
    reussi: bool
    code: int
    sortie: str
    non_negociable: bool = False
    ignore: bool = False


@dataclass
class Verdict:
    resultats: list[Resultat] = field(default_factory=list)

    @property
    def accepte(self) -> bool:
        return all(r.reussi for r in self.resultats if not r.ignore)

    @property
    def echecs(self) -> list[Resultat]:
        return [r for r in self.resultats if not r.reussi and not r.ignore]

    def rapport(self) -> str:
        """Ce que le lead renvoie au worker. La sortie brute, pas un résumé :
        un modèle corrige bien mieux sur l'erreur réelle que sur sa paraphrase."""
        lignes = []
        for r in self.resultats:
            if r.ignore:
                lignes.append(f"[ignoré]  {r.nom}")
            elif r.reussi:
                lignes.append(f"[ok]      {r.nom}")
            else:
                marque = "BLOQUANT" if r.non_negociable else "échec"
                lignes.append(f"[{marque}] {r.nom}  (code {r.code})")
                lignes.append(_indenter(r.sortie))
        verdict = "ACCEPTÉ" if self.accepte else "REFUSÉ"
        lignes.append(f"\n=> {verdict}")
        return "\n".join(lignes)


def _indenter(texte: str, limite: int = 4000) -> str:
    texte = texte.strip()
    if len(texte) > limite:
        # On garde la fin : c'est là que les outils écrivent l'erreur.
        texte = "[...tronqué...]\n" + texte[-limite:]
    return "\n".join("    " + l for l in texte.splitlines())


def charger_controles(config: Path) -> list[dict]:
    projet = yaml.safe_load(config.read_text(encoding="utf-8"))
    return projet.get("porte", []) or []


def executer(
    config: Path,
    worktree: Path,
    ignorer: set[str] | None = None,
) -> Verdict:
    """Exécute la porte sur `worktree`.

    `ignorer` permet de sauter des contrôles hors sujet pour un rendu donné
    (par exemple l'audit npm sur un changement de documentation). Un contrôle
    non négociable n'est jamais ignoré, quoi qu'on demande.
    """
    ignorer = ignorer or set()
    verdict = Verdict()

    for controle in charger_controles(config):
        nom = controle["nom"]
        non_negociable = bool(controle.get("non_negociable"))

        if nom in ignorer and not non_negociable:
            verdict.resultats.append(
                Resultat(nom=nom, reussi=True, code=0, sortie="", ignore=True)
            )
            continue

        try:
            proc = subprocess.run(
                controle["cmd"],
                shell=True,
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=DELAI_MAX_S,
            )
            code, sortie = proc.returncode, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            code, sortie = 124, f"Dépassement du délai ({DELAI_MAX_S} s)."

        verdict.resultats.append(
            Resultat(
                nom=nom,
                reussi=(code == 0),
                code=code,
                sortie=sortie,
                non_negociable=non_negociable,
            )
        )

        # Un bloquant qui tombe arrête tout : inutile de dépenser des minutes
        # de test sur un rendu qui est déjà refusé.
        if code != 0 and non_negociable:
            break

    return verdict


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python porte.py <projet.yaml> <worktree>", file=sys.stderr)
        raise SystemExit(2)

    v = executer(Path(sys.argv[1]), Path(sys.argv[2]))
    print(v.rapport())
    raise SystemExit(0 if v.accepte else 1)
