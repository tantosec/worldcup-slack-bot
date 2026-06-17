# FIFA World Rankings — June 11, 2026 (last official update before WC 2026).
# Lower number = better ranked. Keys match ESPN team displayName values.
#
# Sources (all confirmed identical numbers):
#   https://inside.fifa.com/fifa-world-ranking/men
#   https://www.espn.com/soccer/story/_/id/46664763/fifa-mens-top-50-world-rankings
#   https://www.whereig.com/football/fifa-world-rankings.html
RANKINGS: dict[str, int] = {
    "Argentina":        1,
    "Spain":            2,
    "France":           3,
    "England":          4,
    "Portugal":         5,
    "Brazil":           6,
    "Morocco":          7,
    "Netherlands":      8,
    "Germany":         10,
    "Croatia":         11,
    "Colombia":        13,
    "Mexico":          14,
    "Senegal":         15,
    "Uruguay":         16,
    "United States":   17,
    "Japan":           18,
    "Switzerland":     19,
    "Iran":            20,
    "Türkiye":         22,
    "Ecuador":         23,
    "Austria":         24,
    "South Korea":     25,
    "Australia":       27,
    "Algeria":         28,
    "Egypt":           29,
    "Canada":          30,
    "Norway":          31,
    "Ivory Coast":     33,
    "Panama":          34,
    "Sweden":          38,
    "Czechia":         40,
    "Paraguay":        41,
    "Scotland":        42,
    "Tunisia":         45,
    "Congo DR":        46,
    "Uzbekistan":      50,
    "Qatar":           56,
    "Iraq":            57,
    "South Africa":    60,
    "Saudi Arabia":    61,
    "Jordan":          63,
    "Bosnia-Herzegovina": 64,
    "Cape Verde":      67,
    "Ghana":           73,
    "Curaçao":         82,
    "Haiti":           83,
    "New Zealand":     85,
}

UNKNOWN_RANK = 99


def get_rank(team_name: str) -> int:
    return RANKINGS.get(team_name, UNKNOWN_RANK)
