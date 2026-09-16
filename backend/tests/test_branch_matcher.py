"""
services/branch_matcher.py::match_branch_by_text acepta coincidencias aproximadas (typos
leves) usando BRANCH_NAME_SIMILARITY_THRESHOLD. Estas pruebas fijan ese comportamiento como
regresión: si alguien sube o baja el umbral más adelante, deben fallar aquí antes de fallar en
producción con un cliente real.
"""
from models.branch import Branch
from services.branch_matcher import match_branch_by_text


def _branches(*names):
    return [Branch(name=name) for name in names]


REAL_BRANCHES = _branches(
    "Costa del Este", "San Francisco", "Clayton", "Obarrio", "Via Porras", "Catering"
)


def test_typos_leves_de_sucursales_reales_siguen_matcheando():
    """Errores de tipeo comunes sobre los nombres reales de sucursal deben seguir resolviendo
    a la sucursal correcta (por encima del umbral)."""
    cases = {
        "obario": "Obarrio",
        "clyton": "Clayton",
        "via poras": "Via Porras",
        "sn francisco": "San Francisco",
    }
    for typo, expected_name in cases.items():
        match = match_branch_by_text(typo, REAL_BRANCHES)
        assert match is not None, f"'{typo}' debería matchear alguna sucursal"
        assert match.name == expected_name


def test_texto_sin_relacion_no_matchea_ninguna_sucursal():
    match = match_branch_by_text("quiero saber el horario de hoy", REAL_BRANCHES)
    assert match is None


def test_similar_branch_names_do_not_get_confused():
    """
    Escenario deliberado: dos sucursales con nombres parecidos a propósito ("Obarrio" y
    una nueva "Obarrio Norte"). Un typo sobre una no debe resolver a la otra — la similitud
    con la sucursal correcta debe ser más alta que con la parecida.
    """
    branches = _branches("Obarrio", "Obarrio Norte")

    match_sin_norte = match_branch_by_text("obario", branches)
    assert match_sin_norte is not None
    assert match_sin_norte.name == "Obarrio"

    match_con_norte = match_branch_by_text("obario norte", branches)
    assert match_con_norte is not None
    assert match_con_norte.name == "Obarrio Norte"


def test_umbral_de_similitud_descarta_coincidencias_debiles():
    """Un texto demasiado distinto de cualquier nombre de sucursal no debe forzarse a
    matchear solo porque sea "el menos peor" de la lista."""
    branches = _branches("Obarrio", "Obarrio Norte")
    match = match_branch_by_text("quisiera hacer un pedido para llevar", branches)
    assert match is None
