"""Test della matematica del magazzino.

Si eseguono senza installare nulla:

    python3 -m unittest discover -s test -v

Ogni test descrive una situazione reale dell'officina, non un caso astratto:
serve a impedire che una modifica futura reintroduca un errore di calcolo che
sul report si vedrebbe solo come "un numero un po' strano".
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleo.analisi import analizza_movimenti, stato_materiale  # noqa: E402
from nucleo.impostazioni import Impostazioni  # noqa: E402

OGGI = datetime(2026, 8, 10, 12, 0, 0)


def scarico(giorni_fa, quantita):
    return {"data": OGGI - timedelta(days=giorni_fa), "tipo": "scarico", "quantita": float(quantita)}


def carico(giorni_fa, quantita):
    return {"data": OGGI - timedelta(days=giorni_fa), "tipo": "carico", "quantita": float(quantita)}


def analizza(movimenti, giacenza=100.0, scorta_minima=0.0, scorta_massima=0.0, imp=None):
    return analizza_movimenti(
        movimenti, giacenza_attuale=giacenza, oggi=OGGI,
        scorta_minima=scorta_minima, scorta_massima=scorta_massima, imp=imp,
    )


class TestDatiInsufficienti(unittest.TestCase):
    """Con pochi movimenti il report deve dire "non lo so", non inventare."""

    def test_nessun_movimento(self):
        r = analizza([])
        self.assertFalse(r["dati_sufficienti"])
        self.assertEqual(r["n_movimenti"], 0)
        self.assertIsNone(r["giorni_autonomia_stimati"])
        self.assertIsNone(r["punto_riordino"])

    def test_un_solo_scarico_non_produce_stime(self):
        # Il caso che in passato mostrava "1328 giorni di autonomia".
        r = analizza([carico(90, 100), scarico(80, 2.4)], giacenza=53.1)
        self.assertFalse(r["dati_sufficienti"])
        self.assertIsNone(r["giorni_autonomia_stimati"])
        self.assertIsNone(r["giorni_autonomia_prudente"])
        self.assertIsNone(r["tempo_restock_mediano_giorni"])
        self.assertIsNone(r["trend_consumo_pct"])
        self.assertFalse(r["da_riordinare"])

    def test_storico_troppo_breve_anche_con_molti_movimenti(self):
        # 6 movimenti ma tutti concentrati in 3 giorni: non basta.
        movimenti = [scarico(g, 5) for g in (1, 1, 2, 2, 3, 3)]
        r = analizza(movimenti)
        self.assertFalse(r["dati_sufficienti"])

    def test_consumo_resta_visibile_anche_se_insufficiente(self):
        # Il consumo grezzo e' un fatto osservato, non una stima: si mostra.
        r = analizza([carico(90, 100), scarico(5, 7.5)])
        self.assertFalse(r["dati_sufficienti"])
        self.assertEqual(r["consumo_ultimi_30gg"], 7.5)


class TestFinestreConsumo(unittest.TestCase):
    """Le finestre 0-30 e 30-60 giorni devono coprire tutto senza buchi."""

    def base(self):
        # Storico lungo e abbastanza movimenti perche' le stime siano attive.
        return [carico(120, 500), carico(60, 200), carico(30, 200), scarico(100, 1)]

    def test_movimento_di_oggi_conta_nel_mese_corrente(self):
        # Regressione: la finestra escludeva i movimenti con eta' 0, cioe'
        # proprio quelli registrati il giorno in cui si genera il report.
        r = analizza(self.base() + [scarico(0, 10), scarico(5, 5)])
        self.assertEqual(r["consumo_ultimi_30gg"], 15.0)

    def test_confine_30_giorni_appartiene_alla_finestra_precedente(self):
        r = analizza(self.base() + [scarico(29, 4), scarico(30, 6)])
        self.assertEqual(r["consumo_ultimi_30gg"], 4.0)
        self.assertEqual(r["consumo_30_60gg_fa"], 6.0)

    def test_oltre_60_giorni_non_conta_in_nessuna_finestra(self):
        r = analizza(self.base() + [scarico(59, 3), scarico(60, 99), scarico(80, 99)])
        self.assertEqual(r["consumo_30_60gg_fa"], 3.0)

    def test_trend_confronta_le_due_finestre(self):
        r = analizza(self.base() + [scarico(10, 30), scarico(40, 10)])
        # da 10 a 30 unita' = +200%
        self.assertEqual(r["trend_consumo_pct"], 200.0)

    def test_trend_indefinito_senza_mese_precedente(self):
        r = analizza(self.base() + [scarico(10, 30)])
        self.assertIsNone(r["trend_consumo_pct"])


class TestVelocitaEAutonomia(unittest.TestCase):
    def test_velocita_usa_i_giorni_realmente_coperti(self):
        # Materiale visto per la prima volta 20 giorni fa: dividere per 60
        # sottostimerebbe il consumo e gonfierebbe l'autonomia.
        movimenti = [carico(20, 100), scarico(18, 10), scarico(10, 10), scarico(2, 10)]
        r = analizza(movimenti, giacenza=70)
        self.assertAlmostEqual(r["velocita_consumo_giorno"], 30 / 18, places=2)
        self.assertLess(r["giorni_autonomia_stimati"], 70 / (30 / 60))

    def test_velocita_prudente_segue_il_mese_peggiore(self):
        # Consumo in accelerazione: per gli allarmi si usa il ritmo recente,
        # piu' alto, perche' avvisare tardi costa piu' che avvisare presto.
        movimenti = [carico(90, 500), scarico(50, 10), scarico(40, 10), scarico(10, 100)]
        r = analizza(movimenti, giacenza=200)
        self.assertGreater(r["velocita_prudente_giorno"], r["velocita_consumo_giorno"])
        self.assertLess(r["giorni_autonomia_prudente"], r["giorni_autonomia_stimati"])

    def test_autonomia_assente_senza_consumo(self):
        r = analizza([carico(90, 100), carico(60, 100), carico(30, 100), carico(10, 100)],
                     giacenza=400)
        self.assertIsNone(r["giorni_autonomia_stimati"])
        self.assertFalse(r["da_riordinare"])


class TestCadenzaRifornimento(unittest.TestCase):
    def test_mediana_resiste_al_riordino_anomalo(self):
        # Ordini regolari ogni ~10 giorni piu' un buco di 200: la media
        # direbbe ~57 giorni, la mediana resta sui 10 reali.
        movimenti = [carico(230, 50), carico(30, 50), carico(20, 50), carico(10, 50),
                     scarico(200, 10), scarico(100, 10), scarico(15, 10), scarico(5, 10)]
        r = analizza(movimenti, giacenza=100)
        self.assertEqual(r["tempo_restock_mediano_giorni"], 10.0)

    def test_carichi_dello_stesso_giorno_non_sono_due_rifornimenti(self):
        # Bolla spezzata in due righe: l'intervallo di 0 giorni va ignorato,
        # altrimenti abbassa artificialmente la cadenza.
        movimenti = [carico(40, 50), carico(20, 50), carico(20, 30),
                     scarico(35, 10), scarico(10, 10), scarico(2, 10)]
        r = analizza(movimenti, giacenza=100)
        self.assertEqual(r["tempo_restock_mediano_giorni"], 20.0)

    def test_un_solo_carico_non_da_cadenza(self):
        movimenti = [carico(40, 50), scarico(30, 10), scarico(20, 10), scarico(5, 10)]
        r = analizza(movimenti, giacenza=100)
        self.assertIsNone(r["tempo_restock_mediano_giorni"])
        # Senza storico di riordini si usa il tempo di consegna di default.
        self.assertEqual(r["lead_time_giorni"], float(Impostazioni.lead_time_default_giorni))

    def test_ultimo_carico_e_giorni_trascorsi(self):
        movimenti = [carico(40, 50), carico(12, 50), scarico(30, 10), scarico(5, 10)]
        r = analizza(movimenti, giacenza=100)
        self.assertEqual(r["giorni_da_ultimo_restock"], 12)
        self.assertEqual(r["ultimo_restock"], OGGI - timedelta(days=12))


class TestPuntoDiRiordino(unittest.TestCase):
    """La parte piu' azionabile: quando ordinare e quanto."""

    def movimenti_regolari(self):
        # ~2 unita' al giorno, rifornimenti ogni 10 giorni.
        return [
            carico(50, 100), carico(40, 100), carico(30, 100), carico(20, 100),
            scarico(45, 20), scarico(35, 20), scarico(25, 20), scarico(15, 20),
            scarico(5, 20),
        ]

    def test_punto_riordino_copre_consegna_piu_scorta_minima(self):
        r = analizza(self.movimenti_regolari(), giacenza=200, scorta_minima=30)
        atteso = r["velocita_prudente_giorno"] * r["lead_time_giorni"] + 30
        self.assertAlmostEqual(r["punto_riordino"], round(atteso, 1), places=1)

    def test_scorta_alta_non_richiede_riordino(self):
        r = analizza(self.movimenti_regolari(), giacenza=5000, scorta_minima=30)
        self.assertFalse(r["da_riordinare"])
        self.assertIsNone(r["quantita_da_ordinare"])

    def test_scorta_bassa_richiede_riordino_fino_al_massimo(self):
        r = analizza(self.movimenti_regolari(), giacenza=10,
                     scorta_minima=30, scorta_massima=300)
        self.assertTrue(r["da_riordinare"])
        self.assertEqual(r["quantita_da_ordinare"], 290.0)

    def test_giorni_entro_cui_ordinare_scala_il_tempo_di_consegna(self):
        r = analizza(self.movimenti_regolari(), giacenza=200, scorta_minima=0)
        atteso = max(0, round(r["giorni_autonomia_prudente"] - r["lead_time_giorni"]))
        self.assertEqual(r["giorni_entro_cui_ordinare"], atteso)

    def test_giorni_entro_cui_ordinare_mai_negativo(self):
        r = analizza(self.movimenti_regolari(), giacenza=1, scorta_minima=0)
        self.assertGreaterEqual(r["giorni_entro_cui_ordinare"], 0)


class TestStatoMateriale(unittest.TestCase):
    def riga(self, **campi):
        base = {
            "dati_sufficienti": True, "sotto_scorta": False,
            "da_riordinare": False, "giorni_autonomia_prudente": 90.0,
        }
        base.update(campi)
        return base

    def test_dati_insufficienti_prevale_su_tutto(self):
        classe, testo = stato_materiale(self.riga(dati_sufficienti=False, sotto_scorta=True))
        self.assertEqual(testo, "Dati insufficienti")
        self.assertEqual(classe, "")

    def test_sotto_scorta_e_la_priorita_massima(self):
        _, testo = stato_materiale(self.riga(sotto_scorta=True, da_riordinare=True))
        self.assertEqual(testo, "Sotto scorta")

    def test_da_riordinare(self):
        classe, testo = stato_materiale(self.riga(da_riordinare=True))
        self.assertEqual(testo, "Da riordinare")
        self.assertEqual(classe, "medio")

    def test_in_esaurimento_sotto_la_soglia(self):
        _, testo = stato_materiale(self.riga(giorni_autonomia_prudente=5.0))
        self.assertEqual(testo, "In esaurimento")

    def test_ok(self):
        classe, testo = stato_materiale(self.riga())
        self.assertEqual((classe, testo), ("ok", "OK"))


class TestImpostazioniPersonalizzate(unittest.TestCase):
    def test_soglia_allarme_configurabile(self):
        imp = Impostazioni.da_dizionario({"giorni_allarme_esaurimento": 60})
        riga = {"dati_sufficienti": True, "sotto_scorta": False,
                "da_riordinare": False, "giorni_autonomia_prudente": 45.0}
        self.assertEqual(stato_materiale(riga)[1], "OK")
        self.assertEqual(stato_materiale(riga, imp)[1], "In esaurimento")

    def test_soglie_di_affidabilita_configurabili(self):
        movimenti = [carico(20, 50), scarico(10, 5)]
        self.assertFalse(analizza(movimenti)["dati_sufficienti"])
        imp = Impostazioni.da_dizionario({"movimenti_minimi": 2, "giorni_storico_minimi": 5})
        self.assertTrue(analizza(movimenti, imp=imp)["dati_sufficienti"])

    def test_valori_non_validi_non_rompono_i_default(self):
        imp = Impostazioni.da_dizionario({"movimenti_minimi": "non un numero",
                                          "chiave_inesistente": 1})
        self.assertEqual(imp.movimenti_minimi, 0)  # "non un numero" -> 0.0 -> 0
        self.assertFalse(hasattr(imp, "chiave_inesistente_valorizzata"))

    def test_dizionario_vuoto_o_nullo(self):
        for valore in (None, {}, [], "testo"):
            imp = Impostazioni.da_dizionario(valore)
            self.assertEqual(imp.movimenti_minimi, Impostazioni.movimenti_minimi)


if __name__ == "__main__":
    unittest.main()
