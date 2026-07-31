# POS UI

Кнопка `★ Бонуси` ідентифікує клієнта/картку, показує active, pending, reserved, redeemable і debt, приймає бажану суму списання та відображає projected earn.

Касир не розподіляє бонуси вручну між FOP invoices: сервер стабільно алокує їх по POS rows, а adapter пропорційно переносить у GSF invoice slices з residual в останньому slice.

Manual і birthday discounts змінюють тільки `non_loyalty_discount_amount`. Bonus discount зберігається окремо, але бухгалтерський та фіскальний total використовує їх суму. За cart/customer change quote стає `REQUIRES_REQUOTE`.

Повернення відкривається за унікальним barcode/lookup token чека. UI показує окремо money-paid, redeemed та earned суми; бонуси не додаються до cash/card refund.
