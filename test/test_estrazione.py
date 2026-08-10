"""Test della lettura dal database del gestionale.

Usano un database SQLite in memoria con lo stesso schema di quello reale:
verificano che le stranezze note dei dati veri (giacenza sempre a zero nella
tabella materiali, fornitori a prezzo zero, date sporche) siano gestite come
previsto, senza dover avere sottomano il database dell'officina.
"""

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo import estrazione  # noqa: E402

OGGI = datetime(2026, 8, 10, 12, 0, 0)

SCHEMA = """
CREATE TABLE materiali (
    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, spessore REAL DEFAULT 0,
    prezzo REAL DEFAULT 0, fornitore TEXT DEFAULT '', prezzo_fornitore REAL DEFAULT 0,
    capacita_magazzino REAL DEFAULT 0, giacenza REAL DEFAULT 0,
    categoria_id INTEGER, scorta_minima REAL DEFAULT 0, scorta_massima REAL DEFAULT 0);
CREATE TABLE categorie_materiale (
    id INTEGER PRIMARY KEY, nome TEXT, giacenza_minima REAL DEFAULT 0,
    giacenza_desiderata REAL DEFAULT 0, capacita_magazzino REAL DEFAULT 0, note TEXT DEFAULT '');
CREATE TABLE fornitori (id INTEGER PRIMARY KEY, nome TEXT);
CREATE TABLE materiale_fornitori (
    id INTEGER PRIMARY KEY, materiale_id INTEGER, fornitore_nome TEXT,
    prezzo_fornitore REAL DEFAULT 0, scorta_minima REAL DEFAULT 0,
    scorta_massima REAL DEFAULT 0, giacenza REAL DEFAULT 0);
CREATE TABLE movimenti_magazzino (
    id INTEGER PRIMARY KEY, materiale_id INTEGER, tipo TEXT, quantita REAL,
    data TEXT, note TEXT DEFAULT '', preventivo_id INTEGER, fornitore_nome TEXT DEFAULT '');
CREATE TABLE preventivi (
    id INTEGER PRIMARY KEY, data_creazione TEXT, numero_revisione INTEGER DEFAULT 1,
    preventivo_originale_id INTEGER, costo_totale_materiali REAL DEFAULT 0,
    costi_accessori REAL DEFAULT 0, minuti_taglio REAL DEFAULT 0,
    minuti_avvolgimento REAL DEFAULT 0, minuti_pulizia REAL DEFAULT 0,
    minuti_rettifica REAL DEFAULT 0, minuti_imballaggio REAL DEFAULT 0,
    tot_mano_opera REAL DEFAULT 0, prezzo_cliente REAL DEFAULT 0,
    nome_cliente TEXT DEFAULT '', categoria TEXT DEFAULT '');
CREATE TABLE clienti (id INTEGER PRIMARY KEY, nome TEXT);
"""


class BaseDb(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.addCleanup(self.conn.close)

    def materiale(self, id_, nome, **campi):
        campi.setdefault("prezzo", 0)
        campi.setdefault("scorta_minima", 0)
        campi.setdefault("scorta_massima", 0)
        colonne = ", ".join(["id", "nome"] + list(campi))
        segnaposto = ", ".join(["?"] * (2 + len(campi)))
        self.conn.execute(
            "INSERT INTO materiali (%s) VALUES (%s)" % (colonne, segnaposto),
            [id_, nome] + list(campi.values()),
        )

    def fornitore(self, materiale_id, nome, prezzo, giacenza):
        self.conn.execute(
            "INSERT INTO materiale_fornitori (materiale_id, fornitore_nome, prezzo_fornitore, giacenza)"
            " VALUES (?,?,?,?)",
            (materiale_id, nome, prezzo, giacenza),
        )

    def movimento(self, materiale_id, tipo, quantita, giorni_fa=None, data=None):
        if data is None:
            data = (OGGI - timedelta(days=giorni_fa)).isoformat()
        self.conn.execute(
            "INSERT INTO movimenti_magazzino (materiale_id, tipo, quantita, data) VALUES (?,?,?,?)",
            (materiale_id, tipo, quantita, data),
        )

    def magazzino(self):
        return estrazione.estrai_magazzino(self.conn, OGGI)


class TestGiacenzaEPrezzi(BaseDb):
    def test_giacenza_sommata_dai_fornitori_non_dalla_colonna_materiali(self):
        # Nel database vero materiali.giacenza resta sempre a zero: la
        # giacenza reale e' distribuita per fornitore.
        self.materiale(1, "TWILL", giacenza=0)
        self.fornitore(1, "CIT", 28.5, 390.51)
        self.fornitore(1, "FIBERTECH", 24.9, 131.24)
        riga = self.magazzino()[0]
        self.assertAlmostEqual(riga["giacenza"], 521.75, places=2)

    def test_prezzo_medio_ignora_i_fornitori_a_prezzo_zero(self):
        # Una riga fornitore a zero abbasserebbe la media e falserebbe sia il
        # capitale sia lo storico prezzi.
        self.materiale(1, "CF283")
        self.fornitore(1, "CIT", 0.0, 10)
        self.fornitore(1, "ANGELONI", 20.0, 10)
        riga = self.magazzino()[0]
        self.assertEqual(riga["prezzo_fornitore_medio"], 20.0)

    def test_prezzo_medio_zero_se_nessun_fornitore_ha_prezzo(self):
        self.materiale(1, "SENZA")
        self.fornitore(1, "CIT", 0.0, 47.7)
        self.assertEqual(self.magazzino()[0]["prezzo_fornitore_medio"], 0.0)

    def test_capitale_usa_il_prezzo_del_singolo_fornitore(self):
        # 100 pezzi a 10 EUR + 100 a 30 EUR = 4000, non 200 x media(20) = 4000
        # per caso: qui i lotti sono diversi, quindi il valore differisce.
        self.materiale(1, "MISTO")
        self.fornitore(1, "A", 10.0, 150)
        self.fornitore(1, "B", 30.0, 50)
        riga = self.magazzino()[0]
        self.assertEqual(riga["valore_giacenza"], 150 * 10.0 + 50 * 30.0)
        # La stima approssimata (giacenza totale x prezzo medio) darebbe 4000.
        self.assertNotEqual(riga["valore_giacenza"], riga["giacenza"] * riga["prezzo_fornitore_medio"])

    def test_sotto_scorta(self):
        self.materiale(1, "HS150", scorta_minima=20)
        self.fornitore(1, "CIT", 14.6, 14.15)
        self.assertTrue(self.magazzino()[0]["sotto_scorta"])

    def test_scorta_minima_zero_non_e_mai_sotto_scorta(self):
        self.materiale(1, "LIBERO", scorta_minima=0)
        self.fornitore(1, "CIT", 10.0, 0.0)
        self.assertFalse(self.magazzino()[0]["sotto_scorta"])


class TestDatiSporchi(BaseDb):
    """Un dato rovinato non deve far fallire l'intero report."""

    def test_data_movimento_illeggibile_viene_saltata(self):
        self.materiale(1, "HS300")
        self.fornitore(1, "CIT", 18.3, 100)
        for giorni in (50, 40, 20, 5):
            self.movimento(1, "scarico", 10, giorni_fa=giorni)
        self.movimento(1, "carico", 100, giorni_fa=60)
        # Righe con date impossibili, come capitano da import manuali.
        self.movimento(1, "scarico", 999, data="data-non-valida")
        self.movimento(1, "scarico", 999, data="")
        self.movimento(1, "scarico", 999, data="31/02/2026")

        riga = self.magazzino()[0]
        # Le 5 righe buone sono state analizzate, le 3 sporche ignorate.
        self.assertEqual(riga["n_movimenti"], 5)
        self.assertTrue(riga["dati_sufficienti"])
        self.assertEqual(riga["consumo_ultimi_30gg"], 20.0)

    def test_data_preventivo_illeggibile_viene_saltata(self):
        self.conn.execute(
            "INSERT INTO preventivi (data_creazione, prezzo_cliente) VALUES (?,?)",
            ("2026-05-01T10:00:00", 100.0),
        )
        self.conn.execute(
            "INSERT INTO preventivi (data_creazione, prezzo_cliente) VALUES (?,?)", ("boh", 999.0)
        )
        righe = estrazione.estrai_preventivi(self.conn)
        self.assertEqual(len(righe), 1)
        self.assertEqual(righe[0]["prezzo"], 100.0)

    def test_materiale_senza_movimenti_non_rompe_nulla(self):
        self.materiale(1, "MAI_USATO")
        self.fornitore(1, "CIT", 12.0, 0)
        riga = self.magazzino()[0]
        self.assertFalse(riga["dati_sufficienti"])
        self.assertEqual(riga["stato_testo"], "Dati insufficienti")

    def test_valori_numerici_nulli(self):
        self.conn.execute("INSERT INTO materiali (id, nome, prezzo) VALUES (1, 'NULLO', NULL)")
        self.conn.execute(
            "INSERT INTO materiale_fornitori (materiale_id, fornitore_nome, prezzo_fornitore, giacenza)"
            " VALUES (1, 'CIT', NULL, NULL)"
        )
        riga = self.magazzino()[0]
        self.assertEqual(riga["giacenza"], 0.0)
        self.assertEqual(riga["prezzo_vendita"], 0.0)


class TestAltreEstrazioni(BaseDb):
    def test_confronto_solo_con_almeno_due_fornitori(self):
        self.materiale(1, "UNO")
        self.fornitore(1, "CIT", 10.0, 5)
        self.materiale(2, "DUE")
        self.fornitore(2, "CIT", 20.0, 5)
        self.fornitore(2, "FIBERTECH", 15.0, 5)
        confronto = estrazione.estrai_confronto_fornitori(self.conn)
        self.assertEqual([c["nome"] for c in confronto], ["DUE"])
        self.assertEqual(confronto[0]["risparmio_pct"], 25.0)

    def test_ricarico_ignora_i_materiali_senza_prezzo_fornitore(self):
        self.materiale(1, "CONPREZZO", prezzo=20.0)
        self.fornitore(1, "CIT", 10.0, 1)
        self.materiale(2, "SENZAPREZZO", prezzo=30.0)
        self.fornitore(2, "CIT", 0.0, 1)
        ricarico = estrazione.estrai_ricarico(self.conn)
        self.assertEqual([r["nome"] for r in ricarico], ["CONPREZZO"])
        self.assertEqual(ricarico[0]["ricarico_pct"], 100.0)

    def test_preventivi_calcolano_il_costo_totale(self):
        self.conn.execute(
            "INSERT INTO preventivi (data_creazione, costo_totale_materiali, costi_accessori,"
            " tot_mano_opera, prezzo_cliente) VALUES (?,?,?,?,?)",
            ("2026-05-01T10:00:00", 40.0, 10.0, 25.0, 100.0),
        )
        riga = estrazione.estrai_preventivi(self.conn)[0]
        self.assertEqual(riga["costo_totale"], 75.0)

    def test_revisioni_escluse_dai_preventivi(self):
        self.conn.execute(
            "INSERT INTO preventivi (data_creazione, prezzo_cliente, preventivo_originale_id)"
            " VALUES ('2026-05-01T10:00:00', 50.0, 7)"
        )
        self.assertEqual(estrazione.estrai_preventivi(self.conn), [])

    def test_manodopera_esclude_le_fasi_a_zero(self):
        self.conn.execute(
            "INSERT INTO preventivi (data_creazione, minuti_taglio, minuti_pulizia)"
            " VALUES ('2026-05-01T10:00:00', 30.0, 0)"
        )
        fasi = estrazione.estrai_manodopera(self.conn)
        self.assertEqual([f["fase"] for f in fasi], ["Taglio"])


if __name__ == "__main__":
    unittest.main()
