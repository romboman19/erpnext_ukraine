# ADR-003: Redemption як line discount

Статус: прийнято.

Bonus redemption не є грошовим Mode of Payment. Він зменшує item amount окремим `loyalty_redeemed_amount`; payments покривають лише residual money total. Так бухгалтерський і фіскальний документи мають правильну taxable line base, а refund не повертає bonus-paid суму грошима.
