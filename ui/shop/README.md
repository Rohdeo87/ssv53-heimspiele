# Reservation notification UI

Inline reservation-status.js after the existing script in Bestellverwaltung.tpl.
It only reads the notification-status endpoint; it cannot send or resend mail.
The existing pickup-mail action and its credentials remain unchanged.

After a new reservation is saved, Shop.tpl confirms storage and explains that
confirmation and office notifications are sent automatically. Delivery is driven
by the server timer, never by this browser message or a visit to an old record.
