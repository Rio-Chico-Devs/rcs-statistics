"""Esegue tutti i test del progetto: quelli Python e quelli JavaScript.

    python3 esegui_test.py

Nessuna libreria da installare: i test Python usano ``unittest`` e quelli
JavaScript il test runner incluso in Node. Se Node non e' installato la parte
JavaScript viene saltata con un avviso, senza far fallire il resto.
"""

import os
import shutil
import subprocess
import sys
import unittest

CARTELLA = os.path.dirname(os.path.abspath(__file__))
CARTELLA_TEST = os.path.join(CARTELLA, "test")


def esegui_test_python():
    print("=" * 62)
    print("Test Python (analisi, estrazione, storico prezzi)")
    print("=" * 62)
    suite = unittest.TestLoader().discover(start_dir=CARTELLA_TEST)
    risultato = unittest.TextTestRunner(verbosity=2).run(suite)
    return risultato.wasSuccessful()


def esegui_test_javascript():
    print()
    print("=" * 62)
    print("Test JavaScript (logica del report)")
    print("=" * 62)
    node = shutil.which("node")
    if node is None:
        print("Node non trovato: test JavaScript saltati.")
        print("Non e' un errore: servono solo a chi sviluppa, non per usare il programma.")
        return True
    esito = subprocess.run(
        [node, "--test", os.path.join(CARTELLA_TEST, "test_app.js")],
        cwd=CARTELLA,
    )
    return esito.returncode == 0


def main():
    ok_python = esegui_test_python()
    ok_javascript = esegui_test_javascript()
    print()
    if ok_python and ok_javascript:
        print("Tutti i test sono passati.")
        return 0
    print("Alcuni test sono falliti: vedi sopra.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
