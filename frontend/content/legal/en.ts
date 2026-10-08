import type { LegalDocuments } from "./types";

// Courtesy English translation of content/legal/ru.ts — the Russian text
// prevails. Keep section ids and order identical (enforced by tests).

const OPERATOR_DETAILS = [
  "Operator type: {operatorType}.",
  "Name: {operatorName}.",
  "INN / OGRN / OGRNIP: {operatorRegistration}.",
  "Address: {operatorAddress}.",
  "E-mail: {operatorEmail}.",
  "Roskomnadzor personal-data operator registry number: {rknRegistryNumber}.",
];

export const en: LegalDocuments = {
  terms: {
    title: "Terms of Use",
    summary: "Terms for using {siteAddress} and its analytics services.",
    sections: [
      {
        id: "general",
        heading: "1. General",
        blocks: [
          "These terms govern the relationship between {operatorName} (the “Operator”) and any person using {siteAddress} and its services (the “User”).",
          "By using the site the User confirms having read and accepted these terms. A User who does not agree must stop using the site.",
          "These terms are effective from {effectiveDate}.",
        ],
      },
      {
        id: "service",
        heading: "2. Nature of the service",
        blocks: [
          "The site provides analytical information about football matches: statistical estimates of outcome probabilities produced by mathematical models, historical data and analysis tools (including a strategy backtester and text commentary generated with a language model).",
          "The site is not a gambling operator or a bookmaker, does not accept or settle bets, and is for analytical and informational purposes only. Nothing on the site is an invitation to gamble, a recommendation or financial advice.",
          "Predictions are statistical estimates and guarantee nothing. Past performance of models and strategies does not predict future results.",
        ],
      },
      {
        id: "age",
        heading: "3. Age restriction",
        blocks: [
          "The site is intended only for persons aged 18 or over (18+). By confirming their age on entry, the User declares that they are at least 18.",
        ],
      },
      {
        id: "account",
        heading: "4. Account",
        blocks: [
          "Some features require an account created with an e-mail address and a password. The User undertakes to provide accurate data and not to share the password.",
          "The User is responsible for actions taken with their account and reports any unauthorised access to the Operator at {operatorEmail}.",
          "The Operator may restrict or block an account that breaches these terms, or at the User's request.",
        ],
      },
      {
        id: "tiers",
        heading: "5. Access tiers and promo codes",
        blocks: [
          "Available features and daily limits depend on the access tier (guest, free and paid tiers). Current limits are shown in the interface.",
          "Promo codes grant an access tier or other benefits on the conditions stated when issued, have a limited validity and number of activations, and may be bound to a specific account.",
        ],
      },
      {
        id: "acceptable-use",
        heading: "6. Acceptable use",
        blocks: [
          "The User must not:",
          {
            list: [
              "circumvent the site's limits or technical restrictions, including by creating multiple accounts or spoofing network addresses;",
              "scrape the site without the Operator's written permission;",
              "resell or publicly redistribute the site's analytical materials;",
              "interfere with the site's operation or data security;",
              "use the site in breach of the laws of the Russian Federation.",
            ],
          },
        ],
      },
      {
        id: "ip",
        heading: "7. Intellectual property",
        blocks: [
          "The software, design and analytical materials belong to the Operator or are used by it lawfully. Sports data comes from third-party sources and is used under their terms.",
        ],
      },
      {
        id: "liability",
        heading: "8. Limitation of liability",
        blocks: [
          "The site is provided “as is”. The Operator does not guarantee that data, including live match data that may be delayed, is accurate, complete or timely.",
          "To the extent permitted by the laws of the Russian Federation, the Operator is not liable for decisions the User makes based on the site or for their consequences, including financial losses.",
        ],
      },
      {
        id: "personal-data",
        heading: "9. Personal data",
        blocks: [
          "Personal data is processed under the Personal Data Processing Policy and on the basis of a separate consent to personal data processing.",
          {
            links: [
              { label: "Personal Data Processing Policy", href: "/legal/privacy" },
              { label: "Consent to Personal Data Processing", href: "/legal/consent" },
            ],
          },
        ],
      },
      {
        id: "changes",
        heading: "10. Changes",
        blocks: [
          "The Operator may amend these terms. A new version takes effect when published on this page unless it states otherwise. Continued use of the site means acceptance of the new version.",
        ],
      },
      {
        id: "law",
        heading: "11. Governing law and disputes",
        blocks: [
          "These terms are governed by the laws of the Russian Federation. Claims are sent to the Operator at {operatorEmail}. Disputes not settled by claim are resolved under the laws of the Russian Federation.",
        ],
      },
      {
        id: "contacts",
        heading: "12. Operator details",
        blocks: [{ list: OPERATOR_DETAILS }],
      },
    ],
  },

  privacy: {
    title: "Personal Data Processing Policy",
    summary:
      "How {operatorName} processes and protects the personal data of {siteAddress} users (Article 18.1 of Federal Law No. 152-FZ).",
    sections: [
      {
        id: "general",
        heading: "1. General",
        blocks: [
          "This policy is issued under Federal Law No. 152-FZ of 27 July 2006 “On Personal Data” and sets out how the personal data of {siteAddress} users is processed and protected.",
          "The policy is publicly available and effective from {effectiveDate}.",
        ],
      },
      {
        id: "operator",
        heading: "2. Operator",
        blocks: [{ list: OPERATOR_DETAILS }],
      },
      {
        id: "purposes",
        heading: "3. Purposes, data categories and legal grounds",
        blocks: [
          "The Operator processes only the data needed for the purposes below. No special categories of personal data and no biometric data are processed. No decisions with legal effect are made solely by automated processing.",
          "Purpose 1. Registration, account management and sign-in.",
          {
            list: [
              "Data: e-mail address; password as an irreversible hash (Argon2id); role and access tier; account status and verification flags; the encrypted two-factor authentication secret (when it is turned on; mandatory for administrators); hashes of session and e-mail verification tokens; failed sign-in count and temporary lock time; record creation and update times.",
              "Grounds: performance of an agreement to which the user is a party (Art. 6(1)(5) 152-FZ); the user's consent (Art. 6(1)(1)).",
            ],
          },
          "Purpose 2. Security and abuse prevention.",
          {
            list: [
              "Data: IP address; browser details (User-Agent); a security event log — registration, sign-ins and failed attempts, lockouts, two-factor authentication, session refresh and revocation, promo code redemption, administrator actions; request counters for rate limiting (per IP address, per /64 subnet for IPv6).",
              "Grounds: the Operator's rights and legitimate interests, provided the user's rights are not infringed (Art. 6(1)(7)); performance of the agreement (Art. 6(1)(5)).",
            ],
          },
          "Purpose 3. Providing site features according to the access tier.",
          {
            list: [
              "Data: daily view counters (per account for registered users, per IP address for guests); access-tier subscription details (tier, source, expiry); promo code redemption history (code hash, type, value, status, time); saved backtester strategies (name and parameters).",
              "Ground: performance of the agreement (Art. 6(1)(5)).",
            ],
          },
          "Purpose 4. Sending prediction-change notifications — only if the user enables them.",
          {
            list: [
              "Data: for web notifications, the subscription address issued by the browser's push service and encryption keys; for Telegram, the chat ID; the list of followed matches; hashes of one-time Telegram link tokens; the number of notifications sent per day.",
              "Ground: the user's consent (Art. 6(1)(1)).",
            ],
          },
          "Purpose 5. Remembering interface settings.",
          {
            list: [
              "Data: the chosen language and the age-confirmation flag (stored in cookies in the user's browser, see section 5).",
              "Ground: performance of the agreement (Art. 6(1)(5)).",
            ],
          },
          "Match commentary is generated by a language model from match data only; users' personal data is never sent to the language model.",
        ],
      },
      {
        id: "actions",
        heading: "4. Processing operations",
        blocks: [
          "The Operator processes personal data by automated means, including collection, recording, systematisation, accumulation, storage, clarification (updating, modification), retrieval, use, transfer (provision, access), blocking, deletion and destruction.",
        ],
      },
      {
        id: "cookies",
        heading: "5. Cookies and browser storage",
        blocks: [
          "The site uses only cookies that are necessary for it to work. No advertising or analytics cookies are used.",
          {
            list: [
              "bp_refresh — token that renews the session after sign-in; not readable by page scripts (httpOnly); lifetime {refreshTokenDays} days or until sign-out.",
              "bp_csrf — protection against cross-site request forgery; lifetime {refreshTokenDays} days or until sign-out.",
              "bp_age_ok — confirmation that the visitor is 18+; lifetime {ageGateDays} days, after which age is asked again.",
              "NEXT_LOCALE — the chosen interface language; lifetime {localeCookieDays} days.",
            ],
          },
          "The access token is kept only in the memory of the open tab (valid for {accessTokenMinutes} minutes) and is never written to disk. Session storage remembers whether the disclaimer is collapsed. If the user enables web notifications, a service worker is registered in the browser.",
        ],
      },
      {
        id: "storage",
        heading: "6. Storage location",
        blocks: [
          "Personal data of citizens of the Russian Federation is recorded, systematised, accumulated, stored, clarified and retrieved using databases located in the Russian Federation (Art. 18(5) 152-FZ): {serverLocation}.",
        ],
      },
      {
        id: "retention",
        heading: "7. Retention periods",
        blocks: [
          {
            list: [
              "Account data — for the lifetime of the account and {retentionAccount} after deletion.",
              "Session tokens — {refreshTokenDays} days from issue or until sign-out.",
              "Security event log (including IP addresses and User-Agent) — {retentionAuditLog}.",
              "Notification subscriptions — until the user disables notifications and {retentionPush} afterwards.",
              "Promo code and subscription history — {retentionPromo}.",
              "Backups — {retentionBackups}.",
              "Rate-limit and daily-limit counters — from one minute to the end of the current UTC day.",
            ],
          },
          "Once the purposes are achieved or consent is withdrawn, personal data is destroyed within 30 days unless federal law provides otherwise (Art. 21 152-FZ).",
        ],
      },
      {
        id: "transfer",
        heading: "8. Disclosure and cross-border transfer",
        blocks: [
          "The Operator does not sell personal data. Data may be disclosed to:",
          {
            list: [
              "processors acting on the Operator's instructions under a contract (for example, a hosting provider), in accordance with Art. 6(3) 152-FZ;",
              "public authorities where required by the laws of the Russian Federation.",
            ],
          },
          "Cross-border transfer happens only for notifications the user enables:",
          {
            list: [
              "Telegram — chat ID and notification text;",
              "browser vendors' push services (determined by the user's browser) — subscription address and encrypted notification content.",
            ],
          },
          "Cross-border transfer is made with the user's consent and after notifying Roskomnadzor under Art. 12 152-FZ. The user can disable notifications at any time, which stops the transfer.",
          "Sports data providers and the language model never receive users' personal data.",
        ],
      },
      {
        id: "security",
        heading: "9. Security measures",
        blocks: [
          {
            list: [
              "a person responsible for organising personal data processing has been appointed;",
              "passwords are stored only as irreversible hashes (Argon2id); session, e-mail verification and Telegram link tokens and promo codes are stored as hashes;",
              "two-factor authentication secrets and external service keys are stored encrypted;",
              "traffic between the browser and the server is protected by HTTPS;",
              "session cookies are not readable by page scripts and are restricted by SameSite; cross-site request forgery protection and a strict content security policy (CSP) are applied;",
              "sign-in attempts and other sensitive actions are rate-limited, and accounts are temporarily locked after failed attempts;",
              "administrative functions are role-based and require two-factor authentication; administrator actions are logged;",
              "processing is limited to the data needed for the stated purposes.",
            ],
          },
        ],
      },
      {
        id: "rights",
        heading: "10. Data subject rights and requests",
        blocks: [
          "The user may:",
          {
            list: [
              "obtain information about the processing of their personal data (Art. 14 152-FZ);",
              "require clarification, blocking or destruction of data that is incomplete, outdated, inaccurate, unlawfully obtained or not necessary for the stated purpose;",
              "withdraw consent to personal data processing;",
              "appeal the Operator's actions or inaction to Roskomnadzor or a court.",
            ],
          },
          "Requests are sent to {operatorEmail} or by post to {operatorAddress}. A request must contain details that identify the user (for example, the account e-mail address) and their relationship with the Operator.",
          "The Operator responds within 10 working days of receipt. This period may be extended by no more than 5 working days with a reasoned notice.",
          "Account deletion together with the related data is done on request sent to {operatorEmail}.",
        ],
      },
      {
        id: "responsible-person",
        heading: "11. Person responsible for personal data processing",
        blocks: ["{responsiblePerson}, e-mail: {operatorEmail}."],
      },
      {
        id: "changes",
        heading: "12. Changes",
        blocks: ["The Operator may amend this policy. A new version takes effect when published on this page."],
      },
    ],
  },

  consent: {
    title: "Consent to Personal Data Processing",
    summary: "Separate consent of a {siteAddress} user to the processing of personal data.",
    sections: [
      {
        id: "grant",
        heading: "1. Granting consent",
        blocks: [
          "I, a user of {siteAddress}, freely, of my own will and in my own interest, give {operatorType} {operatorName} (INN / OGRN / OGRNIP: {operatorRegistration}, address: {operatorAddress}) (the “Operator”) consent to process my personal data on the terms below.",
          "Consent is given by ticking the corresponding box in a form on the site (at registration or when enabling notifications) and is a separate document, not part of the terms of use or the personal data processing policy.",
        ],
      },
      {
        id: "purposes",
        heading: "2. Purposes",
        blocks: [
          {
            list: [
              "registration, account management and sign-in;",
              "security and abuse prevention;",
              "providing site features according to the access tier;",
              "sending prediction-change notifications — if I enable notifications.",
            ],
          },
        ],
      },
      {
        id: "data",
        heading: "3. Personal data covered",
        blocks: [
          {
            list: [
              "e-mail address;",
              "IP address and browser details (User-Agent);",
              "account, access tier, redeemed promo code and saved strategy details;",
              "if notifications are enabled — web subscription address and encryption keys, Telegram chat ID, list of followed matches.",
            ],
          },
        ],
      },
      {
        id: "actions",
        heading: "4. Processing operations and methods",
        blocks: [
          "Collection, recording, systematisation, accumulation, storage, clarification (updating, modification), retrieval, use, transfer (provision, access), blocking, deletion and destruction of personal data by automated means.",
        ],
      },
      {
        id: "transfer",
        heading: "5. Disclosure and cross-border transfer",
        blocks: [
          "I agree to disclosure of my personal data to processors acting on the Operator's instructions. If I enable notifications, I agree to cross-border transfer of the chat ID and notification text to Telegram, and of the subscription address and encrypted notification content to my browser's push service.",
        ],
      },
      {
        id: "term",
        heading: "6. Duration and withdrawal",
        blocks: [
          "This consent is valid until the purposes are achieved, the account is deleted or consent is withdrawn.",
          "Consent can be withdrawn by a request sent to {operatorEmail} or by post to {operatorAddress}. After withdrawal the Operator stops processing and destroys the personal data within 30 days unless federal law provides otherwise. Withdrawal may make features that require an account unavailable.",
        ],
      },
      {
        id: "policy",
        heading: "7. Acknowledgement of the policy",
        blocks: [
          "I confirm that I have read the Personal Data Processing Policy.",
          { links: [{ label: "Personal Data Processing Policy", href: "/legal/privacy" }] },
        ],
      },
    ],
  },

  responsible: {
    title: "Responsible Gaming",
    summary: "The 18+ restriction, warning signs of problem gambling and where to get help.",
    sections: [
      {
        id: "age",
        heading: "18+ only",
        blocks: [
          "The site is only for people aged 18 or over. It does not accept bets and is not a gambling operator: we publish statistical estimates, not recommendations to bet.",
        ],
      },
      {
        id: "principles",
        heading: "Remember",
        blocks: [
          {
            list: [
              "A prediction is a probability, not a guarantee. Unlikely outcomes happen regularly.",
              "Past performance of models and strategies does not predict future results.",
              "Betting is not a way to earn money or solve financial problems.",
            ],
          },
        ],
      },
      {
        id: "warning-signs",
        heading: "Warning signs of problem gambling",
        blocks: [
          {
            list: [
              "you spend more money or time on betting than you planned;",
              "you try to win back losses;",
              "you borrow money or sell things to bet;",
              "you hide your betting from people close to you;",
              "you feel anxious or irritable when you cannot bet;",
              "betting interferes with work, study or relationships.",
            ],
          },
        ],
      },
      {
        id: "self-control",
        heading: "Staying in control",
        blocks: [
          {
            list: [
              "decide in advance how much you can afford to lose and never exceed it;",
              "limit your time and take breaks;",
              "do not bet when stressed, tired or intoxicated;",
              "do not chase losses.",
            ],
          },
          "On request we can block your account on this site. Write to {operatorEmail}.",
        ],
      },
      {
        id: "help",
        heading: "Where to get help",
        blocks: [
          {
            links: [
              {
                label: "Gamblers Anonymous",
                href: "https://www.gamblersanonymous.org/",
                description: "an international mutual-support fellowship for people with a gambling problem",
              },
              {
                label: "Gambling Therapy",
                href: "https://www.gamblingtherapy.org/",
                description: "free online support for people affected by gambling and their families",
              },
            ],
          },
          "[LOCAL RUSSIAN HELP RESOURCE]",
        ],
      },
    ],
  },

  disclaimer: {
    title: "Disclaimer",
    summary: "The analytical nature of the information on the site, 18+.",
    sections: [
      {
        id: "nature",
        heading: "Analytical information only",
        blocks: [
          "Analytical and informational purposes only. Predictions are statistical estimates, guarantee nothing, and are not gambling advice or a financial recommendation. 18+.",
        ],
      },
      {
        id: "no-bookmaker",
        heading: "The site does not accept bets",
        blocks: [
          "The site is not a gambling operator or a bookmaker, does not accept bets and does not advertise bookmakers.",
        ],
      },
      {
        id: "data",
        heading: "Data and models",
        blocks: [
          "Sports data comes from third-party sources and may be incomplete, inaccurate or delayed; when live data is delayed the match card shows a notice. Models can be wrong.",
          "Backtester results are computed on historical data. Past performance does not predict future results.",
          "Text commentary is generated by a language model from the statistical models' outputs; it is not a source of probabilities or betting advice.",
        ],
      },
      {
        id: "responsibility",
        heading: "Responsibility",
        blocks: [
          "Users make decisions based on the site's information on their own and at their own risk.",
          { links: [{ label: "Responsible Gaming", href: "/legal/responsible" }] },
        ],
      },
    ],
  },
};
