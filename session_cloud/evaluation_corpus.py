"""Public synthetic known-answer corpus. PDF bytes are generated only in tests."""

DOCUMENTS = [
    ("energy.pdf", [
        ("SOL-17", "The Amber solar farm produces twelve megawatts of electricity from sunlight. Battery storage provides power after sunset.", "solar panels renewable clean electricity grid sunlight battery storage"),
        ("WIND-42", "The coastal wind installation generates eighteen megawatts using offshore turbines. Maintenance is scheduled every October.", "wind turbine offshore coastal maintenance technicians energy generation"),
        ("HYD-09", "The river hydro station produces seven megawatts. Fish ladders preserve salmon migration during the spring spawning season.", "river hydroelectric dam fish ladder salmon migration spring habitat"),
        ("CELL-63", "The reserve battery uses lithium iron phosphate chemistry. Its tested backup duration is six hours at the hospital's critical load.", "battery hospital emergency reserve energy chemistry backup duration"),
        ("GRID-88", "The microgrid disconnects automatically during a regional blackout. The island controller restores critical loads in ninety seconds.", "microgrid blackout outage island controller automatic recovery emergency"),
        ("HEAT-31", "The district heating network captures waste heat from the data center. Insulated pipes deliver hot water to two hundred homes.", "district heating waste heat hot water insulated pipes homes winter"),
    ]),
    ("clinical.pdf", [
        ("MED-71", "The trial medicine produces nausea and dizziness as adverse reactions. Participants must report severe symptoms to the study physician.", "medicine treatment trial patient adverse reactions nausea dizziness physician"),
        ("LAB-54", "Blood samples must be refrigerated at four degrees Celsius. Transport to the laboratory must finish within two hours.", "blood sample refrigerator cold transport laboratory temperature collection"),
        ("RESP-26", "The breathing exercise reduces panic through slow diaphragmatic inhalation. Participants practice for ten minutes each morning.", "breathing diaphragm inhalation exhalation anxiety relaxation exercise practice"),
        ("TRIAL-90", "The randomized study enrolled one hundred and twenty adults. The control group received a placebo for eight weeks.", "randomized study placebo control adults trial participants duration outcome"),
        ("SLEEP-16", "The sleep protocol requires a dark bedroom and consistent bedtime. Caffeine is prohibited for six hours before sleep.", "sleep bedtime bedroom dark caffeine rest circadian protocol evening"),
        ("VACC-38", "The vaccine storage freezer requires minus twenty degrees Celsius. Temperature excursions trigger immediate quarantine of the affected batch.", "vaccine storage freezer temperature batch quarantine cold chain safety"),
    ]),
    ("operations.pdf", [
        ("AUTH-7K", "Authentication recovery codes are single use. Administrators require two hardware security keys for privileged account access.", "authentication security hardware administrator recovery privilege access code"),
        ("RETAIN-93", "The records policy retains invoices for seven years. Expired documents require approval from the records officer before secure deletion.", "invoice retention records expired document approval secure deletion policy"),
        ("NET-28", "Network incident escalation begins with the on-call engineer. A critical outage must be reported within fifteen minutes.", "network incident outage escalation engineer report operations alert"),
        ("BACK-65", "Database backups run nightly at midnight UTC. Restore rehearsals occur monthly using an isolated recovery environment.", "database backup restore rehearsal recovery isolated nightly midnight operations"),
        ("DEPLOY-84", "Application releases use a canary stage serving five percent of traffic. Elevated error rates automatically roll back the release.", "deployment canary traffic release error rollback application safety"),
        ("WASTE-52", "Electronic waste is collected quarterly by a certified recycler. Storage devices must be cryptographically erased before collection.", "electronic waste recycler quarterly storage devices erase collection disposal"),
    ]),
]

QUESTIONS = [
    ("What is the capacity of SOL-17?", ["SOL-17"], "keyword"),
    ("What medicine side effects should patients watch for?", ["MED-71"], "semantic"),
    ("How does the clinic keep blood specimens cold during delivery?", ["LAB-54"], "semantic"),
    ("How long can the hospital keep running on stored emergency electricity?", ["CELL-63"], "semantic"),
    ("Which plants protect fish travelling upstream?", ["HYD-09"], "semantic"),
    ("What happens if a software release starts failing?", ["DEPLOY-84"], "semantic"),
    ("How many hardware keys does AUTH-7K require?", ["AUTH-7K"], "keyword"),
    ("What is the RETAIN-93 invoice retention period?", ["RETAIN-93"], "keyword"),
    ("When are BACK-65 backups and restore drills performed?", ["BACK-65"], "keyword"),
    ("How are temperature problems handled for VACC-38?", ["VACC-38"], "keyword"),
    ("Compare emergency blackout recovery and network outage escalation.", ["GRID-88", "NET-28"], "multi_pdf"),
    ("Compare battery hospital backup with database backup recovery.", ["CELL-63", "BACK-65"], "multi_pdf"),
]


def paragraphs(document):
    # Distinct 170-ish word pages exercise real overlapping child/parent packing,
    # not a vector database with one preassigned answer per document.
    return [f"{code}. {fact} " + " ".join(
        f"{vocabulary} {code}. {fact}" for _ in range(6)) for code, fact, vocabulary in document]
