# api/calendar/register-webhook

[`route.ts`](route.ts) — points Vexa's own `meeting.started` webhook at the sales-cycle add-on
after a rep connects a calendar, so its customer-mapping/feature-request pipeline reacts as soon
as a calendar-synced call starts — instead of a rep hand-running `PUT /user/webhook` themselves.
Never overwrites a `webhook_url` the account already has configured for something else of its own.
