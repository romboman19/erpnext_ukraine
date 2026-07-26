# Gate 0F evidence — ownership conversion and partner return

- Date: 2026-07-13
- Site: `postest.local`
- Company: `POS Test Ukraine`
- Run: `5A5B88BDFE`
- Result: `PASS WITH WAREHOUSE-VALUATION BOUNDARY`

Production was not opened, migrated, restarted or written. The runner used a
temporary source copy in the test backend. Every submitted Stock Entry,
Purchase Invoice, Payment Entry and Sales Invoice was cancelled in reverse
dependency order. All seven test ownership-lot balances returned to zero, and
the original Serial/Batch setting was restored from `0` to `0`.

## Reproducible runner

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.ownership.run_ownership_conversion_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

The runner is test-site allow-listed and is neither imported by hooks nor
exposed as an API.

## Commission UAH and partial partner return

Event `TP-G0F-5A5B88BDFE-COM-UAH` received three zero-valued commission units.
It removed two units for conversion, returned one unit to the partner and
received two own units through Purchase Invoice `ACC-PINV-2026-00004`.

| Evidence | Result |
|---|---:|
| Third-party source balance after conversion + return | 0 |
| Purchase Invoice stock value / payable | 160 UAH |
| Payment Entries | 1 × 160 UAH |
| Outstanding after payment | 0 |
| Converted-unit sale COGS | 80 UAH |
| Own quantity after one-unit sale | 1 |

The partner return was a zero-valued Material Issue and created no stock asset
or Supplier liability.

## Commission USD and two payouts

Event `TP-G0F-5A5B88BDFE-COM-USD` converted two units at 10 USD each with a
provisional rate of 40 UAH/USD.

| Evidence | Result |
|---|---:|
| Purchase Invoice obligation | 20 USD |
| Stock asset / base payable | 800 UAH |
| Payment 1 | 10 USD / 410 UAH |
| Payment 2 | 10 USD / 420 UAH |
| Outstanding after both payments | 0 USD |
| Net exchange result, payment 1 | 10 UAH loss |
| Net exchange result, payment 2 | 20 UAH loss |

ERPNext used the site's current party exchange rate inside each Payment Entry
and balanced the Purchase Invoice allocation through a system Exchange
Gain/Loss Journal Entry. The individual system JE amounts were not the economic
exchange result by themselves. Summing the Payment Entry exchange line and its
linked system JE produced the expected 10 and 20 UAH losses.

## Consignment UAH

Event `TP-G0F-5A5B88BDFE-CON-UAH` removed one zero-valued consignment unit and
received one own unit through Purchase Invoice `ACC-PINV-2026-00006` at 70 UAH.
One 70 UAH Payment Entry cleared the Supplier outstanding to zero.

## Serialized return

Two Serial Nos were received for the same commission ownership lot. The runner
explicitly returned `TP-G0F-5A5B88BDFE-SER-SER-1`; its warehouse became empty,
while `...-SER-2` remained in the commission warehouse. The ownership guard ran
before submit and the dimension balance changed from two to one. Cleanup then
cancelled the outward and inward bundles and restored the active balance to
zero.

## Mixed own-stock valuation boundary

The three conversions added stock value of `160 + 800 + 70 = 1,030 UAH` to the
same technical OWN warehouse. The runner then sold a row carrying the USD
conversion ownership lot while one older 80 UAH own unit remained.

- selected ownership-lot acquisition cost: 400 UAH per unit;
- actual outgoing COGS: 80 UAH;
- total COGS across both test sales: 160 UAH;
- remaining aggregate Stock Asset: 870 UAH;
- remaining dimension quantities: one unit in each of the three own lots.

This is expected ERPNext warehouse FIFO behavior. Inventory Dimension protects
quantity but does not create a valuation queue. See [ADR 0006](../../adr/0006-ownership-conversion-and-valuation.md).

## Required implementation constraints

1. Conversion and partner return are explicit authorized events, not ordinary
   warehouse transfers.
2. Conversion uses a zero-valued third-party issue plus a stock-updating
   Purchase Invoice into OWN; partner return uses only the source issue.
3. Generated documents require stable event links, idempotency and reverse-order
   compensation.
4. Foreign-currency reconciliation combines Payment Entry and system exchange
   JE evidence.
5. Untracked own-stock COGS is reported at warehouse valuation scope, not
   ownership-lot scope.
6. Batch/Serial return requires exact selection and ownership validation.
