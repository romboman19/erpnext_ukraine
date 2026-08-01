# State machines

Certificate: Issued → Reserved For Sale/Payment Pending → Active → Partially/Fully Redeemed. Blocked, Expired, Refunded, Replaced and Manual Review are explicit terminal/control states.

Reservation: Active → Consuming → Consumed, or Active → Released/Expired. Consuming is not expired by TTL worker.

POS recovery states distinguish Reserved, Payment In Progress, Locally Completed, Fiscal Pending and Manual Review.
