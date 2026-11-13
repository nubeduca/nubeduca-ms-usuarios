"""
Copia los JSON Schema de nubeduca-contratos a contratos/esquemas/ para que se desplieguen
con las Lambdas. Córrelo cada vez que cambie el catálogo.

    python scripts/copiar_esquemas.py                      # busca ../nubeduca-contratos
    python scripts/copiar_esquemas.py ruta/a/nubeduca-contratos
    python scripts/copiar_esquemas.py --solo lms.content    # solo los eventos de tu source

Conserva el esquema de ejemplo de la plantilla (lms.plantilla.*) hasta que lo borres.
"""

import argparse
import shutil
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "contratos" / "esquemas"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("contratos", nargs="?", default=str(RAIZ.parent / "nubeduca-contratos"))
    parser.add_argument("--solo", help="prefijo de source, por ejemplo lms.content")
    args = parser.parse_args()

    origen = Path(args.contratos) / "eventos" / "esquemas"
    if not origen.is_dir():
        raise SystemExit(f"No encuentro {origen}. Clona nubeduca-contratos o pasa su ruta.")
    DESTINO.mkdir(parents=True, exist_ok=True)
    copiados = 0
    for archivo in sorted(origen.glob("*.json")):
        if args.solo and not (archivo.name.startswith(args.solo + ".") or archivo.name.startswith("_")):
            continue
        shutil.copy2(archivo, DESTINO / archivo.name)
        copiados += 1
    print(f"{copiados} esquemas copiados a {DESTINO}")


if __name__ == "__main__":
    main()
