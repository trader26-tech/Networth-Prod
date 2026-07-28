"""
Static knowledge base for the covered call RAG assistant.
Each chunk is a focused explanation of one concept.
"""

CHUNKS = [
    {
        "id": "cc_overview",
        "text": (
            "A covered call with NiftyBees works like this: you buy NiftyBees ETF shares (which track "
            "Nifty 50 at ~1/100 the price) and simultaneously sell (write) an OTM Nifty 50 CE option. "
            "The ETF holding hedges you against Nifty moves while the short call generates income. "
            "You collect the call premium upfront — this income is yours regardless of what Nifty does. "
            "Full coverage means: shares = lots × lot_size × (spot / niftybees_price), which ensures "
            "1 share of NiftyBees exposure for every 1 Nifty unit in the option position. "
            "This strategy is neutral-to-moderately-bullish: you profit when Nifty stays flat or rises "
            "moderately, but cap your upside at the strike price."
        ),
        "topic": "overview",
    },
    {
        "id": "cc_pnl_zones",
        "text": (
            "P&L zones at expiry for a NiftyBees covered call: "
            "LOSS ZONE: Nifty settles below (entry_spot - premium). The call expires worthless (you keep "
            "premium), but NiftyBees losses exceed the premium collected. Net loss = shares × "
            "(ST - S0)/100 + lots × lot_size × premium. "
            "BREAKEVEN: Nifty = S0 - premium. Total P&L = 0. "
            "PROFIT ZONE: Nifty between breakeven and strike. ETF gains partially offset by nothing (call "
            "expires worthless), full premium kept. P&L = lots × lot_size × (ST - S0 + premium). "
            "MAX PROFIT ZONE (capped): Nifty at or above strike at expiry. P&L is flat = "
            "lots × lot_size × (K - S0 + premium). You cannot earn more regardless of how high Nifty goes. "
            "The premium received is your downside buffer — it shifts the breakeven below entry."
        ),
        "topic": "pnl_zones",
    },
    {
        "id": "cc_delta",
        "text": (
            "Delta for a covered call position: the NiftyBees ETF has delta ≈ +1 (moves roughly 1-for-1 "
            "with Nifty). The short call has delta between 0 and -1 (negative because you sold it). "
            "Net position delta = +1 (ETF) - call_delta. For a 0.3 delta OTM call, net delta ≈ 0.7, "
            "meaning you participate in about 70% of Nifty upside moves. "
            "As Nifty rises toward your strike, call delta approaches 1 and your net delta approaches 0 "
            "(you're fully hedged — you stop making money). "
            "As Nifty falls, call delta drops toward 0 and net delta approaches +1 (full long exposure). "
            "A low-delta OTM call (0.20-0.30) gives more upside participation but less premium."
        ),
        "topic": "greeks_delta",
    },
    {
        "id": "cc_theta",
        "text": (
            "Theta (time decay) is the covered call writer's best friend. Every day that passes, the "
            "call you sold loses time value — this decay accrues to you as profit. "
            "Theta is highest when the option is ATM and accelerates in the final 2 weeks before expiry. "
            "A short call with 30 DTE might have theta of ₹5/day; the same call with 7 DTE might decay ₹15/day. "
            "This means the last week is the most profitable period per day for the premium seller. "
            "However, high gamma in the final days means a sudden Nifty spike can wipe out theta gains. "
            "For a covered call held to expiry, total theta collected = entire extrinsic (time) value of "
            "the premium, assuming the call stays OTM. "
            "Best practice: sell calls with 20-45 DTE to balance premium collection vs gamma risk."
        ),
        "topic": "greeks_theta",
    },
    {
        "id": "cc_vega_iv",
        "text": (
            "Vega measures sensitivity to Implied Volatility (IV). As a seller of calls, you are SHORT vega "
            "— you benefit when IV drops after you sell. "
            "Sell covered calls when IV is relatively HIGH (India VIX > 15-18) to collect fat premiums. "
            "Avoid selling calls when IV is very low — premium is thin and not worth the risk. "
            "If IV spikes after you sell, your short call becomes more expensive (mark-to-market loss), "
            "but this is a temporary paper loss; at expiry only intrinsic value matters. "
            "IV crush after events (RBI policy, budget, earnings) is very profitable for call sellers — "
            "sell before the event if IV is elevated, collect the premium, benefit from IV dropping. "
            "The IV shown in the app is the Black-Scholes implied volatility extracted from the option price."
        ),
        "topic": "greeks_vega",
    },
    {
        "id": "cc_itm_situation",
        "text": (
            "When the short call goes In-The-Money (ITM) — Nifty rises above your strike: "
            "1. You still profit but gains are capped. Your NiftyBees ETF gained but the short call is "
            "offsetting those gains. "
            "2. Assignment risk: At expiry, if call is ITM, you may be assigned (obligated to deliver). "
            "With cash-settled Nifty options, this means a cash debit for the intrinsic value, offset by "
            "your NiftyBees gains. "
            "3. Margin: When the call is deep ITM, your broker requires additional margin because the "
            "MTM loss on the short call is large. Extra margin needed ≈ MTM_loss × 1.1 (10% buffer). "
            "You must pledge NiftyBees or add cash to avoid forced square-off. "
            "4. Options: (a) Hold — max profit is guaranteed if Nifty stays above strike at expiry. "
            "(b) Roll up — buy back the call and sell a higher strike for more credit. "
            "(c) Exit both legs if thesis is wrong."
        ),
        "topic": "itm_situation",
    },
    {
        "id": "cc_rolling_up",
        "text": (
            "Rolling up: buy back your short call and sell a higher-strike call, usually for a net debit. "
            "Why roll up: Nifty has risen above your strike and you want to recapture more upside. "
            "Example: sold 24000 CE for ₹120, Nifty now at 24200. Buy back 24000 CE at ₹250 (loss), "
            "sell 24500 CE for ₹180 (net debit = ₹70). Your new max profit level is now 24500. "
            "Roll up costs money (net debit) but extends your profit zone upward. "
            "Only roll up if you believe Nifty will continue higher. "
            "If Nifty is already near expiry, rolling up may not be worth it — consider just closing. "
            "Net credit roll up is possible if you also roll out in time (diagonal roll)."
        ),
        "topic": "rolling_up",
    },
    {
        "id": "cc_rolling_down",
        "text": (
            "Rolling down: buy back your short call and sell a lower-strike call for a net credit. "
            "Why roll down: Nifty has fallen and you want to collect more premium to offset ETF losses, "
            "or to lower your breakeven. "
            "Example: sold 24000 CE, Nifty falls to 23000. Buy back 24000 CE at ₹20 (cheap), "
            "sell 23500 CE for ₹80 (net credit ₹60). You collect more premium, reducing breakeven further. "
            "Risk: if Nifty bounces back above your new lower strike, you get capped at a lower level. "
            "Rolling down increases downside protection at the cost of limiting upside more aggressively. "
            "Useful if you believe Nifty will stay in a lower range or continue declining."
        ),
        "topic": "rolling_down",
    },
    {
        "id": "cc_rolling_forward",
        "text": (
            "Rolling forward (time roll): close the expiring short call and re-sell the same strike in "
            "the next expiry, collecting more premium. "
            "Why roll forward: your short call is nearing expiry (low premium remaining, high gamma risk) "
            "and you want to continue the covered call income strategy. "
            "Example: 24000 CE with 5 DTE is worth only ₹15. Buy it back, sell next month's 24000 CE "
            "for ₹90 — net credit of ₹75 collected. "
            "Best time to roll forward: 5-7 DTE, when theta is maxed out and gamma risk is high. "
            "You can also roll forward + up simultaneously (diagonal): next month higher strike, "            "collecting credit while moving the cap higher."
        ),
        "topic": "rolling_forward",
    },
    {
        "id": "cc_margin",
        "text": (
            "Margin for the short call: SEBI requires SPAN + Exposure margin for short options. "
            "For Nifty CE, this is typically 15-20% of the notional (lots × lot_size × spot). "
            "When the call goes ITM, the MTM loss is added to the required margin. "
            "Extra margin needed when ITM ≈ (current_call_value - premium_collected) × lots × lot_size × 1.1. "
            "The 1.1x buffer is because SEBI/exchanges add a volatility margin. "
            "You can pledge NiftyBees ETF shares as collateral — typically 90% of market value is "
            "accepted as margin, minus any haircut applied by your broker. "
            "If margin falls below the required level, the broker issues a margin call and may "
            "square off positions. Always keep 20-30% extra margin buffer."
        ),
        "topic": "margin",
    },
    {
        "id": "cc_niftybees_avgdown",
        "text": (
            "Averaging down NiftyBees when Nifty falls: if Nifty drops significantly after entry, "
            "your NiftyBees ETF is showing a loss. You can average down by buying more shares at the "
            "lower price to reduce your average cost. "
            "Averaging formula: buy the same number of shares as you currently hold. "
            "New average price = (original_price + current_price) / 2. "
            "Cost to average down = shares × current_niftybees_price. "
            "This reduces the breakeven of your NiftyBees position but requires additional capital. "
            "Combined with rolling down the call (collecting more premium), averaging down is a "
            "valid recovery strategy for a fallen covered call. "
            "Warning: only average down if you have strong conviction Nifty will recover. "
            "Averaging into a sustained bear market compounds losses."
        ),
        "topic": "avgdown",
    },
    {
        "id": "cc_exit_rules",
        "text": (
            "When to exit the covered call strategy: "
            "1. PROFIT TARGET: If you've captured 50-80% of max premium (theta has done its job early), "
            "close the short call, keep ETF. This reduces gamma risk. "
            "2. LOSS STOP: If total position loss exceeds 2x the premium collected, exit both legs. "
            "The premium was your buffer — if it's exhausted and you're still losing, thesis is wrong. "
            "3. STRUCTURAL BREAK: If Nifty breaks a major support level decisively (confirmed close), "
            "consider exiting NiftyBees to avoid further ETF losses. "
            "4. EXPIRY APPROACH: With <5 DTE and call OTM, let it expire worthless (max theta). "
            "With <5 DTE and call ITM, either roll forward or close the short call to remove gamma. "
            "5. EVENT RISK: Close before major binary events (RBI, elections) if IV is low — "
            "re-enter after the event."
        ),
        "topic": "exit_rules",
    },
    {
        "id": "cc_strike_selection",
        "text": (
            "Choosing the right strike for your short call: "
            "ATM or slightly OTM (0-2% above spot): high premium, but upside is immediately capped. "
            "Best for bearish-neutral view. High delta (0.45-0.50). "
            "Moderately OTM (2-4% above spot): balanced. Good premium with some room to run. "
            "Delta typically 0.25-0.40. Most popular for income generation. "
            "Far OTM (4%+ above spot): low premium but lots of upside participation. "
            "Delta < 0.20. Best for bullish view with income enhancement. "
            "For Nifty monthly expiry, selling 2-3% OTM calls with 30 DTE at IV > 15 is a common "
            "institutional approach. For weekly expiry, 1-2% OTM is typical. "
            "Use the distance% column in the strike table to gauge OTM-ness. "
            "Avoid selling calls below the breakeven level of your ETF position."
        ),
        "topic": "strike_selection",
    },
    {
        "id": "cc_black_scholes",
        "text": (
            "Black-Scholes pricing for Nifty options: the app uses BS to estimate option value before expiry. "
            "Inputs: S (current Nifty), K (strike), T (years to expiry), IV (implied vol), r (risk-free rate ~6.5%). "
            "BS gives a theoretical price based on lognormal distribution assumption. "
            "For checking your P&L before expiry: use the target date slider to see the BS estimated value "
            "of your short call at a future date and Nifty level. "
            "The cyan dashed line on the P&L chart shows estimated P&L using BS pricing at the selected "
            "target date — this accounts for remaining time value in the option. "
            "At expiry, BS = intrinsic value only (max(S-K, 0)). Before expiry, BS > intrinsic value "
            "because of time value (extrinsic value). "
            "The difference between BS price and intrinsic value is the time value you would lose by "
            "closing the position early vs. holding to expiry."
        ),
        "topic": "black_scholes",
    },
    {
        "id": "cc_position_sizing",
        "text": (
            "Position sizing for NiftyBees covered call: "
            "Coverage ratio: shares of NiftyBees ÷ (lots × lot_size) × 100. 100% coverage is ideal. "
            "Capital required ≈ NiftyBees cost (largest component) + option margin (15-20% of notional). "
            "Example: 1 lot Nifty = 75 units. At Nifty 23000, NiftyBees ≈ ₹230. "
            "Shares needed = 75 × (23000/230) ≈ 7500 shares. NiftyBees cost = 7500 × 230 = ₹17.25 lakh. "
            "Option margin ≈ 75 × 23000 × 18% = ₹3.1 lakh. Total capital ≈ ₹20 lakh per lot. "
            "Income per lot (monthly): ₹120 premium × 75 = ₹9000 (~0.45% monthly on capital). "
            "Annual yield ≈ 5-8% on top of Nifty returns. "
            "Do not over-leverage — stick to 1-2 lots per ₹20-25 lakh of capital."
        ),
        "topic": "position_sizing",
    },
]
