# Model, execution semantics, and identification limits

## 1. Research question

The object of the study is an **execution-aware comparison**, not a claim that an analytical quoting formula creates alpha. We ask how inventory control, implementation choices, and trading costs change the performance of four fixed policies on identical event tapes.

Prices are measured in ticks and quantities in lots. A tick–lot of P&L converts to quote currency by multiplication by `tick_size × lot_size`. Inventory is signed: positive means long. The synthetic instrument uses a $0.01 tick and a unit lot. The historical adapters normalize real ETHUSDT and BTCUSDT futures quantities to 0.001 base-asset lots; their configured price ticks are $0.01 and $0.10 respectively. These are experiment inputs for the sampled historical instruments, not a dynamically queried exchange specification service.

## 2. Policies

The passive baseline quotes the current best bid and ask, subject to exactly the same inventory, latency, fee, participation, and liquidation rules as the other policies.

The Avellaneda–Stoikov approximation uses the reservation price and total spread

$$r_t=m_t-q_t\gamma\widehat\sigma_t^2\tau_t,$$

$$w_t=\gamma\widehat\sigma_t^2\tau_t+\frac{2}{\gamma}\log\left(1+\frac{\gamma}{\kappa}\right),$$

with quoted prices obtained by rounding $r_t-w_t/2$ down and $r_t+w_t/2$ up to ticks. Quotes are constrained to be passive relative to the latest observed BBO. The finite risk horizon is capped: $\tau_t=\min(T-t,30\text{ seconds})$. This deliberately prevents a long tape from mechanically producing enormous early-session inventory penalties.

- **Fixed A–S:** holds the training variance estimate constant.
- **Adaptive A–S:** updates a causal EWMA variance estimate.
- **Imbalance A–S:** adds half the current spread times the normalized displayed-depth imbalance to the adaptive reservation price. This is an untrained heuristic extension; it is not presented as an estimated predictive signal.

The model's unit-order continuous-price approximation is a policy heuristic when applied to integer ticks, finite queues, multi-lot orders, nonzero fees, and a switching market. We do not claim to solve the optimal control problem for the full replay environment.

### Risk aversion is a declared budget

Rather than tune $\gamma$ on evaluation P&L, calibration sets

$$\gamma=\frac{3h_{\mathrm{train}}}{Q_{\max}\widehat\sigma_{\mathrm{train}}^2 H},$$

where $h$ is the training median half-spread and $H=30$ seconds. At the inventory cap, the training-volatility reservation shift over $H$ is three half-spreads. The policy can be reproduced without a hidden optimization loop.

## 3. Causal calibration

All calibration observations precede all observations used to compute evaluated P&L. Synthetic paths reserve the first quarter for fitting. Historical sessions reserve their first 30 minutes; flat-start evaluations follow in six nonoverlapping 15-minute windows. January 1/2 start at 00:00 UTC and January 3 starts at 11:00 UTC. These blocks are not statistically independent. The strategy knows its explicitly supplied scheduled end time, not the timestamp of a future final message or future prices. `Tape.window` carries the requested boundary even when no message arrives exactly there; inventory risk is integrated through that boundary and the latest available BBO prices the close.

For volatility, a fixed one-second grid takes the last available mid-price at or before each grid time. The initial estimate is the mean squared grid increment divided by its interval. In replay, elapsed-time EWMA updates use only the book already observed:

$$v_t=(1-a_t)v_{t^-}+a_t\frac{(m_t-m_{t^-})^2}{\Delta t},\qquad a_t=1-2^{-\Delta t/30}.$$

The replay update is triggered by an available quote after at least one second, so its sampling times need not coincide exactly with the calibration grid. No trade price is substituted for an unobserved future midpoint.

At each training trade, the contemporaneous pre-trade book supplies the midpoint. Let $N_d$ count aggressive trades reaching at least distance $d$ from that midpoint. We fit

$$\mu_d=\Delta A e^{-\kappa d},\qquad
\ell(A,\kappa)=\sum_d[N_d\log\mu_d-\mu_d],$$

using positivity transforms and bounded numerical optimization. Zero-count bins remain in the objective, avoiding the arbitrary pseudocount needed by a log-count OLS regression.

**This is a composite likelihood.** The same trade can contribute to several nested distance counts. The terms are dependent, so we do not report ordinary Poisson standard errors from the Hessian. The fitted intensity describes *potential trade reach*, not an identified fill rate for a new order. The backtester subsequently imposes queue and participation constraints. Optimizer status and the observed/fitted count curve are saved.

## 4. Event ordering and latency

For every timestamp, the engine proceeds in this order:

1. Acknowledge an older pending cancel/replace if its latency has expired.
2. Match the incoming aggressive trade against eligible resting orders.
3. Update the observed BBO when the event is a quote.
4. Update causal volatility and mark current inventory.
5. If due, schedule a replacement based on the state now known.

An order can fill only when `activated < event.time`. Even with zero configured latency, an order submitted in response to an event cannot execute against that event or another event at the same timestamp. One pending request is allowed; an earlier order remains live until replacement arrives. If an acknowledged replacement would cross the current BBO, it is rejected as post-only. Identical-price existing orders retain their remaining size and priority; a replenishment or price change requires a new queue position.

Historical book and trade archives have exchange transaction timestamps. The merged tape stably orders trades before quote updates on equal timestamps. Cross-stream ordering at millisecond resolution is not fully identified. The model assumes immediate market-data observation and varies order/cancel latency; it does not model separate feed, execution-report, and gateway delays. This limitation is explicit rather than hidden inside a generic latency parameter.

## 5. Queue model and partial fills

Front-queue places the hypothetical order ahead of displayed size. Back-queue places displayed same-side BBO size ahead, multiplied by a scenario factor. If the quote improves the BBO, the model assigns no displayed queue ahead. For deeper quotes, BBO depth is only a proxy: the input is not a full-depth order book.

An aggressive sell can fill a bid only when its recorded price is at or below our bid; an aggressive buy analogously reaches an ask. Trade volume first reduces modeled queue ahead. The actual execution quantity is

$$u=\left\lfloor\min\left(Q_{\mathrm{remaining}},(V-Q_{\mathrm{ahead}})^+,
\rho V,Q_{\max}-s q\right)\right\rfloor,$$

where $s$ is our side and $\rho=0.05$ is the participation cap. This both permits partial fills and prevents a print from supplying unlimited volume. Orders do not receive cancellation credit when displayed size falls. Even a trade-through print is volume constrained and retains the conservative queue proxy. This can underfill deep orders; it is intentional and testable.

Front/back are **alternative assumptions, not proven P&L bounds**. More fills can worsen adverse selection or inventory. The counterfactual order does not change the recorded tape; participation limits reduce but do not eliminate this structural weakness. Exact queue position requires richer order-level data.

## 6. Accounting and risk

A fill of size $u$ at price $p$ on our side $s\in\{-1,+1\}$ changes state by

$$q'=q+su,\qquad C'=C-spu-f|pu|.$$

The signed fee rate permits rebates. The inventory limit is checked at both acknowledgement and execution, including fills that occurred during pending replacement latency. Terminal P&L is cash after flattening inventory at the final opposite BBO and paying the specified taker fee. The model separately reports whether that liquidation exceeds displayed top-level depth; it does not silently claim a depth sweep.

Risk measures include the exact event-interval inventory integral,

$$q_{\mathrm{RMS}}=\sqrt{\frac1T\int_0^Tq_t^2\,dt},$$

maximum absolute inventory, marked-equity drawdown, terminal inventory before close, turnover, and fill count. An accounting decomposition separates immediate spread capture, inventory revaluation, maker fees, and closing costs. There is no annualization of a few minutes of data.

## 7. Synthetic identification experiment

The generator emits quarter-second book observations with low/high volatility regimes and aggressive trades at intermediate times. Trade directions influence a price change emitted afterward; the policy cannot observe that response before the trade. Exponential trade sizes and geometrically distributed price penetration create heterogeneous fills. A fresh latent imbalance variable influences trade directions within an interval, but its displayed depth is only emitted at the interval end; a new independent variable is drawn for the next interval. Consequently, the depth imbalance available to the strategy is lagged and does **not** predict subsequent trade signs in this generator. The imbalance-skew policy is therefore a negative-control-style feature check here, not a fair benchmark for a trained predictive microprice signal. Its poor synthetic outcome cannot establish that informative order-book imbalance is generally unhelpful. The historical depth feature is separate and receives no claim of predictive validation.

The generator is a stress-testing instrument. Its parameters are not asserted to be a complete empirical model of an exchange. Synthetic and historical evidence are reported separately.

## 8. Statistical interpretation

The independent unit is a synthetic **path**, not a trade. Policy differences are paired on identical paths, including their shared calibration data. The report gives percentile bootstrap intervals from 3,000 path resamples. The factorial grid reuses seeds across execution assumptions, improving comparisons without multiplying the number of independent realizations.

Historical 15-minute blocks share dates and market conditions. With only a few dates, the report gives descriptive outcomes and exact conditional cost break-even levels; it does not manufacture narrow IID confidence intervals from thousands of fills. Hyperparameters and time windows are recorded in the protocol and manifests.

An exploratory risk-preference plot evaluates mean P&L minus a specified penalty on mean squared inventory. It is a preference calculation over tested policies, not a separately validated optimized trading strategy.

## References

- [Avellaneda and Stoikov (2008), *High-frequency trading in a limit order book*](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf), DOI [10.1080/14697680701381228](https://doi.org/10.1080/14697680701381228).
- [HftBacktest: exchange and queue-model assumptions](https://hftbacktest.readthedocs.io/en/latest/order_fill.html), used as a conceptual reference for limitations of replay; this project does not call or copy its execution engine.
- [Binance public-data schema and integrity documentation](https://github.com/binance/binance-public-data).
