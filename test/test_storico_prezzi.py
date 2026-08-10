"""Test dell'archivio prezzi fornitore.

Coprono soprattutto i casi in cui il file su disco e' rovinato: l'archivio e'
scritto dal programma ma vive accanto all'eseguibile, dove qualcuno puo'
aprirlo, modificarlo o troncarlo. Nessuno di questi casi deve impedire la
generazione del report.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo import storico_prezzi  # noqa: E402

OGGI = datetime(2026, 8, 10, 12, 0, 0)


def riga(materiale_id, prezzo, nome="X"):
    return {"materiale_id": materiale_id, "nome": nome, "prezzo_fornitore_medio": prezzo}


class BaseArchivio(unittest.TestCase):
    """Ogni test lavora su un archivio temporaneo, mai su quello vero."""

    def setUp(self):
        self.cartella = tempfile.TemporaryDirectory()
        self.percorso = os.path.join(self.cartella.name, "storico_prezzi.json")
        patcher = mock.patch.object(storico_prezzi, "percorso_archivio", lambda: self.percorso)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.cartella.cleanup)

    def scrivi(self, contenuto):
        with open(self.percorso, "w", encoding="utf-8") as f:
            f.write(contenuto)

    def leggi(self):
        with open(self.percorso, "r", encoding="utf-8") as f:
            return json.load(f)


class TestRegistrazione(BaseArchivio):
    def test_prima_rilevazione_per_ogni_materiale(self):
        storico_prezzi.registra([riga(1, 18.3), riga(2, 14.6)], OGGI)
        salvato = self.leggi()
        self.assertEqual(sorted(salvato.keys()), ["1", "2"])
        self.assertEqual(salvato["1"][0]["prezzo_fornitore"], 18.3)

    def test_prezzo_invariato_non_crea_doppioni(self):
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        storico_prezzi.registra([riga(1, 18.3)], OGGI + timedelta(days=1))
        storico_prezzi.registra([riga(1, 18.3)], OGGI + timedelta(days=2))
        self.assertEqual(len(self.leggi()["1"]), 1)

    def test_prezzo_cambiato_aggiunge_una_rilevazione(self):
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        storico_prezzi.registra([riga(1, 21.96)], OGGI + timedelta(days=30))
        voci = self.leggi()["1"]
        self.assertEqual(len(voci), 2)
        self.assertEqual(voci[-1]["prezzo_fornitore"], 21.96)

    def test_prezzo_assente_non_viene_tracciato(self):
        # Un materiale senza prezzo fornitore valido non deve entrare
        # nell'archivio con uno zero, che falserebbe ogni variazione futura.
        storico_prezzi.registra([riga(1, 0.0), riga(2, -5.0)], OGGI)
        self.assertFalse(os.path.exists(self.percorso))

    def test_nessuna_scrittura_se_nulla_e_cambiato(self):
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        primo_mtime = os.path.getmtime(self.percorso)
        os.utime(self.percorso, (primo_mtime - 100, primo_mtime - 100))
        storico_prezzi.registra([riga(1, 18.3)], OGGI + timedelta(days=1))
        self.assertEqual(os.path.getmtime(self.percorso), primo_mtime - 100)

    def test_salvataggio_non_lascia_file_temporanei(self):
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        residui = [f for f in os.listdir(self.cartella.name) if f.endswith(".tmp")]
        self.assertEqual(residui, [])


class TestArchivioRovinato(BaseArchivio):
    def test_json_non_valido(self):
        self.scrivi("{questo non e' json!!!")
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        self.assertEqual(self.leggi()["1"][0]["prezzo_fornitore"], 18.3)

    def test_json_di_tipo_sbagliato(self):
        self.scrivi("[1, 2, 3]")
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        self.assertIn("1", self.leggi())

    def test_voce_di_materiale_non_e_una_lista(self):
        self.scrivi('{"1": "rovinato"}')
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        self.assertEqual(self.leggi()["1"][0]["prezzo_fornitore"], 18.3)

    def test_rilevazione_senza_prezzo(self):
        self.scrivi('{"1": [{"data": "2026-01-01T00:00:00"}]}')
        storico_prezzi.registra([riga(1, 18.3)], OGGI)
        self.assertEqual(self.leggi()["1"][-1]["prezzo_fornitore"], 18.3)

    def test_annota_ignora_le_rilevazioni_malformate(self):
        archivio = {"1": [{"data": "x"}, "non un dizionario", {"prezzo_fornitore": 10.0, "data": "2026-01-01"}]}
        righe = [riga(1, 12.0)]
        storico_prezzi.annota(righe, archivio)
        self.assertEqual(righe[0]["n_rilevazioni_prezzo"], 1)
        self.assertEqual(righe[0]["primo_prezzo_rilevato"], 10.0)


class TestVariazioni(BaseArchivio):
    def annota_con(self, prezzi):
        archivio = {"1": [{"data": OGGI.isoformat(), "prezzo_fornitore": p} for p in prezzi]}
        righe = [riga(1, prezzi[-1] if prezzi else 0)]
        storico_prezzi.annota(righe, archivio)
        return righe[0]

    def test_una_sola_rilevazione_non_da_variazione(self):
        r = self.annota_con([18.3])
        self.assertEqual(r["n_rilevazioni_prezzo"], 1)
        self.assertIsNone(r["variazione_prezzo_pct"])

    def test_aumento_percentuale(self):
        r = self.annota_con([20.0, 24.0])
        self.assertEqual(r["variazione_prezzo_pct"], 20.0)

    def test_diminuzione_percentuale(self):
        r = self.annota_con([20.0, 15.0])
        self.assertEqual(r["variazione_prezzo_pct"], -25.0)

    def test_variazione_calcolata_sulla_prima_rilevazione(self):
        # Deve confrontare l'ultimo prezzo col primo osservato, non col
        # penultimo: interessa l'aumento complessivo dall'inizio.
        r = self.annota_con([10.0, 11.0, 12.0, 15.0])
        self.assertEqual(r["variazione_prezzo_pct"], 50.0)

    def test_materiale_mai_rilevato(self):
        righe = [riga(99, 5.0)]
        storico_prezzi.annota(righe, {})
        self.assertEqual(righe[0]["n_rilevazioni_prezzo"], 0)
        self.assertIsNone(righe[0]["primo_prezzo_rilevato"])


if __name__ == "__main__":
    unittest.main()
