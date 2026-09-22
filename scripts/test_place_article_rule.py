"""What the place-article rule must and must not accept.

Every extract below is the real opening of the article Wikipedia returns for
that club name, taken from logs/queued_place_audit.json.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from place_article_rule import fold, judge, strip_brackets  # noqa: E402

ACCEPT = [
    ("Agropoli",
     "Agropoli is a town and comune located in the Cilento area of the "
     "province of Salerno, Campania, Italy.",
     ("Agropoli", "Campania", "Italy")),
    # nested brackets: the square pair sits inside the round one
    ("Kuşadası",
     "Kuşadası (Turkish: [ˈkuʃadasɯ]) is a municipality and district of "
     "Aydın Province, Turkey.",
     ("Kuşadası", "", "Turkey")),
    # the Italian spelling of commune
    ("Tortona",
     "Tortona (Italian: [torˈtoːna]; Latin: Dertona) is a comune of "
     "Piemonte, in the Province of Alessandria, Italy.",
     ("Tortona", "Piemonte", "Italy")),
    # two names for one place: the first is the one that must be tested
    ("Mogi das Cruzes",
     "Mogi das Cruzes (Portuguese pronunciation: [moˈʒi] or [muˈʒi das "
     "ˈkɾuzis]) is a municipality in the state of São Paulo, Brazil.",
     ("Mogi das Cruzes", "", "Brazil")),
]

REFUSE = [
    # the whole point of the rule: a real town, a real article, and no
    # relationship to the club that was asked about
    ("Chêne", "Montreux (UK: , US: ; French: [mɔ̃tʁø] ; Arpitan: Montrolx) "
              "is a Swiss municipality and town on the shoreline of Lake Geneva."),
    ("Firenze", "Florence (Italian: Firenze) is the capital and most populous "
                "city of the Italian region of Tuscany."),
    # an area is not an address: this club plays in Hong Kong
    ("South China", "South China (simplified Chinese: 华南) is a geographical "
                    "and cultural region that covers the southernmost part of China."),
    # not about a place at all
    ("Donar", "Thor (from Old Norse: Þórr) is a prominent god in Germanic paganism."),
    ("Vasco da Gama", "Vasco da Gama was a Portuguese explorer and nobleman "
                      "who was the first European to reach India by sea."),
    ("Falcon", "Falcons are birds of prey in the genus Falco, which includes "
               "about 40 species."),
    ("Larisa", "Larisa may refer to:"),
]


def test_a_name_that_matches_is_accepted():
    for team, extract, want in ACCEPT:
        got = judge(team, extract)
        assert got["verdict"] == "accept", (team, got)
        assert (got["city"], got["state"], got["country"]) == want, (team, got)
    print(f"test_a_name_that_matches_is_accepted PASS ({len(ACCEPT)})")


def test_everything_else_is_refused():
    for team, extract in REFUSE:
        got = judge(team, extract)
        assert got["verdict"] != "accept", (team, got)
    print(f"test_everything_else_is_refused PASS ({len(REFUSE)})")


def test_brackets_come_off_however_they_nest():
    assert strip_brackets("Kuşadası (Turkish: [ˈkuʃadasɯ]) is a town") \
        .startswith("Kuşadası is"), strip_brackets("Kuşadası (Turkish: [x]) is a town")
    # a truncated extract can cut a bracket in half
    assert "(" not in strip_brackets("Shumen (Bulgarian: Шумен, also romanized")
    print("test_brackets_come_off_however_they_nest PASS")


def test_the_comparison_folds_punctuation_and_accents():
    assert fold("Al-Jahra") == fold("Al Jahra")
    assert fold("Kuşadası") == fold("Kusadasi")
    # folding must not make two different names equal
    assert fold("Pristina") != fold("Prishtina")
    print("test_the_comparison_folds_punctuation_and_accents PASS")


def test_the_rule_never_writes_a_stint():
    """It sets a club's location only. Stints are a separate, later decision."""
    src = (Path(__file__).resolve().parent / "place_article_rule.py").read_text(
        encoding="utf-8")
    assert "career_history" not in src, "the rule must not touch stints"
    print("test_the_rule_never_writes_a_stint PASS")


if __name__ == "__main__":
    test_a_name_that_matches_is_accepted()
    test_everything_else_is_refused()
    test_brackets_come_off_however_they_nest()
    test_the_comparison_folds_punctuation_and_accents()
    test_the_rule_never_writes_a_stint()
    print("\nall place-article-rule tests PASS")
