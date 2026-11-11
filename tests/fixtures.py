"""Trimmed-down copies of real API response shapes for each job board."""

from datetime import UTC, datetime, timedelta

RECENT = (datetime.now(UTC) - timedelta(days=2)).isoformat()
RECENT_MS = int((datetime.now(UTC) - timedelta(days=2)).timestamp() * 1000)

GREENHOUSE = {
    "jobs": [
        {
            "id": 4012345,
            "title": "Backend Engineer, Payments",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4012345",
            "location": {"name": "Remote - US"},
            "updated_at": RECENT,
            "first_published": RECENT,
            "departments": [{"id": 1, "name": "Engineering"}],
            "content": "&lt;p&gt;Build &lt;strong&gt;payment&lt;/strong&gt; APIs.&lt;/p&gt;"
            "&lt;ul&gt;&lt;li&gt;3+ years Python&lt;/li&gt;&lt;li&gt;Postgres&lt;/li&gt;&lt;/ul&gt;",
        },
        {
            "id": 4012346,
            "title": "Engineering Manager",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4012346",
            "location": {"name": "New York, NY"},
            "updated_at": RECENT,
            "content": "",
        },
    ]
}

LEVER = [
    {
        "id": "a1b2c3d4-0000-0000-0000-000000000001",
        "text": "Software Engineer",
        "hostedUrl": "https://jobs.lever.co/globex/a1b2c3d4",
        "applyUrl": "https://jobs.lever.co/globex/a1b2c3d4/apply",
        "categories": {"location": "Durham, NC", "team": "Platform", "commitment": "Full-time"},
        "workplaceType": "hybrid",
        "descriptionPlain": "Globex builds logistics software.",
        "lists": [{"text": "Requirements", "content": "<li>Python</li><li>AWS</li>"}],
        "createdAt": RECENT_MS,
        "salaryRange": {"min": 110000, "max": 140000, "currency": "USD", "interval": "per-year-salary"},
    }
]

ASHBY = {
    "jobs": [
        {
            "id": "f00d-1",
            "title": "Machine Learning Engineer",
            "location": "San Francisco",
            "isRemote": True,
            "isListed": True,
            "jobUrl": "https://jobs.ashbyhq.com/initech/f00d-1",
            "applyUrl": "https://jobs.ashbyhq.com/initech/f00d-1/application",
            "descriptionPlain": "Train ranking models.",
            "publishedAt": RECENT,
            "department": "AI",
            "compensation": {
                "summaryComponents": [
                    {
                        "compensationType": "Salary",
                        "currencyCode": "USD",
                        "interval": "1 YEAR",
                        "minValue": 150000,
                        "maxValue": 190000,
                    }
                ]
            },
        },
        {
            # Real-world quirk: isRemote is true on a hybrid, office-based role.
            "id": "f00d-3",
            "title": "Software Engineer, Frontend",
            "location": "New York, NY (HQ)",
            "isRemote": True,
            "workplaceType": "Hybrid",
            "secondaryLocations": [{"location": "San Francisco, CA"}],
            "isListed": True,
            "jobUrl": "https://jobs.ashbyhq.com/initech/f00d-3",
            "descriptionPlain": "React.",
            "publishedAt": RECENT,
        },
        {"id": "f00d-2", "title": "Hidden", "isListed": False, "jobUrl": "x"},
    ]
}
