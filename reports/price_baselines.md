# Tomato at Kolar: 7-day modal forecast

Primary market: Kolar APMC.
Prices are Agmarknet modal prices in rupees per quintal.
The model is a ridge regression. Features on the day the forecast is made: that day's modal, the modal about 7 and 30 days earlier, arrivals over those 7 days, the change over 7 days, and the modal from the same point in the previous year.
Holdout: the most recent 90 days of forecast origins whose 7-day outcome is already known.
For a 7-day horizon, "tomorrow equals today" means the forecast equals the modal on the day the forecast is made.
"Same week last year" means the modal about 365 days before the target day.

Holdout origins: 2026-07-28 to 2026-10-26 (90 days). Training rows: 175.

- Model weighted absolute percentage error: 20.59%
- Tomorrow-equals-today weighted absolute percentage error: 22.26%
- Same-week-last-year weighted absolute percentage error: 39.12%

On this holdout the model is lower than both baselines.

The 30-day number shown in the product is the same season last year, shifted by 30 days. It is not a second trained model.

## Two dated calls

### spike on 2026-08-11

Modal that day: Rs 1660. Model 7-day forecast: Rs 1325. Modal about 7 days later: Rs 800.
Call: sell nearby. Bengaluru modal Rs 1900 covered transport of Rs 140 per quintal with Rs 100 left over.
Cost of being wrong: Rs 0 per quintal. The price did not rise after the sell call, so nothing was left on the table.

### costliest call on 2026-08-04

Modal that day: Rs 730. Model 7-day forecast: Rs 823. Modal about 7 days later: Rs 1660.
Call: sell nearby. Bengaluru modal Rs 2100 covered transport of Rs 140 per quintal with Rs 100 left over.
Cost of being wrong: Rs 930 per quintal. Rupees per quintal left on the table because the price rose after a sell call.

The other nine crops do not get this model. Their sell or store call is whether today's modal is at least 5 percent above the same week last year.
On the live line a soft tomato or banana price is delay harvest, leave the fruit on the vine for 3 to 4 days. A harvested load of those two is a sell: they last 3 to 5 days without cold storage. A soft price for the other eight crops is a dry godown.
The spoken quote is the modal plus that day's minimum and maximum, because grade moves the same mandi on the same morning. An FPO gate price can replace the lagged Agmarknet modal before calls go out.
Shortage, for every crop, means 7-day arrivals under 60 percent of the same week last year and a modal in the top fifth of the last 90 days.
