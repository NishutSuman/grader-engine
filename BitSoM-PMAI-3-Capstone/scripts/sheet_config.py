"""Shared constants for the 4 BITSoM PMAI-3 problem-statement sheets.

These are standalone Google Sheets, NOT part of GradePilot's own DB — this
whole project is deliberately kept out of the app (see ../README.md for why).
Auth reuses the same service-account token helper the app already has
(grader.results_sheet._token), so no separate credentials are needed as long
as .env + the service-account key are in place per the main repo's SETUP.md.
"""

SHEETS = {
    "PS1": "12Pea4bedP1pA2bZyEBDJ3CTP5vIIUoosUaLtsbfSa7s",  # "PS 1 - TA 1"
    "PS2": "1c5aolEYIL38EkYTYSWnDuzR7cQVOMfIuyQGEiWdfSgw",  # "PS 2 - TA 2/ TA 5"
    "PS3": "1NzCbTfyZl6HsEfPgF5oZU8Z47Bz74dCT8RTCu5X2opo",  # "PS 3 - TA 3 / TA5"
    "PS4": "1W_7J6NDPA72D04FlKOpJQYQerLCWLMg0hZwqaTVzvuU",  # "PS 4 - TA 4 / TA6"
}

TAB = "Group-Mapping"  # the only tab that matters — see README's "Sheet structure" section

# Row 4 onward is data; rows 1-3 are a merged 3-row header (category banner /
# column name / atomized-criterion text). Column layout varies slightly by
# sheet (PS3 is offset by 1 column) — always resolve indices at runtime by
# searching the header text, never hardcode column letters/indices.
