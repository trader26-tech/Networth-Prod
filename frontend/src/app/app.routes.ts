import { Routes } from '@angular/router';
import { DashboardComponent } from './components/dashboard/dashboard';
import { StrategyComponent } from './components/strategy/strategy';
import { SettingsComponent } from './components/settings/settings';
import { ScannerComponent } from './components/scanner/scanner';
import { CoveredCallComponent } from './components/covered-call/covered-call';
import { PortfolioComponent } from './components/portfolio/portfolio';
import { CcSimulatorComponent } from './components/cc-simulator/cc-simulator';
import { StrategyMathComponent } from './components/strategy-math/strategy-math';
import { MathHandbookComponent } from './components/math-handbook/math-handbook';
import { StrategiesHubComponent } from './components/strategies-hub/strategies-hub';
import { ProtectedWheelComponent } from './components/protected-wheel/protected-wheel';
import { ShortStrangleComponent } from './components/short-strangle/short-strangle';
import { HedgesComponent } from './components/hedges/hedges';
import { SavedStrategiesComponent } from './components/saved-strategies/saved-strategies';
import { SmartMoneyComponent } from './components/smart-money/smart-money';
import { ScorecardComponent } from './components/scorecard/scorecard';
import { ExitStrategyComponent } from './components/exit-strategy/exit-strategy';
import { PeScannerComponent } from './components/pe-scanner/pe-scanner';
import { Networth } from './components/networth/networth';
import { NetworthImport } from './components/networth-import/networth-import';
import { Land } from './components/land/land';
import { LandTax } from './components/land-tax/land-tax';
import { Apartments } from './components/apartments/apartments';
import { ApartmentsTax } from './components/apartments-tax/apartments-tax';
import { Gold } from './components/gold/gold';
import { StockAccounts } from './components/stock-accounts/stock-accounts';
import { Equity } from './components/equity/equity';
import { StockTax } from './components/stock-tax/stock-tax';
import { Fno } from './components/fno/fno';
import { Bonds } from './components/bonds/bonds';
import { AionionImport } from './components/aionion-import/aionion-import';
import { BondsTax } from './components/bonds-tax/bonds-tax';
import { Salary } from './components/salary/salary';
import { Ulip } from './components/ulip/ulip';
import { Lic } from './components/lic/lic';
import { Fd } from './components/fd/fd';
import { Expenses } from './components/expenses/expenses';
import { OtherIncome } from './components/other-income/other-income';
import { Cash } from './components/cash/cash';
import { Documents } from './components/documents/documents';
import { Loans } from './components/loans/loans';
import { Reminders } from './components/reminders/reminders';
import { Home } from './components/home/home';
import { House } from './components/house/house';

export const routes: Routes = [
  // Home — unified command-center dashboard (net worth, CAGR, income, advisors)
  { path: '',                  component: Home },
  { path: 'networth',          component: Networth },
  { path: 'networth/import',   component: NetworthImport },
  // Land & real-estate — parcels with auto CAGR + title documents
  { path: 'land',              component: Land },
  // Land Tax — "Bhoomi" capital-gains chatbot: sell-now tax, restructuring, gift-to-family
  { path: 'land-tax',          component: LandTax },
  // Apartments — rented flats: appreciation + rent → overall CAGR.
  // Apartment tax ("Ashish": rental income + Sec-54) has BOTH a page and a FAB on /apartments.
  { path: 'apartments',        component: Apartments },
  { path: 'apartments-tax',    component: ApartmentsTax },
  // Bay Villa — TVH A-64 purchase: cost sheet, payment schedule & funding plan
  { path: 'house',             component: House },
  // Land + Build was merged into Apartments — keep old links working
  { path: 'built',             redirectTo: 'apartments', pathMatch: 'full' },
  // Gold / Silver — live-priced precious-metal pieces
  { path: 'gold',              component: Gold },
  // Stocks — live multi-account holdings; "Kuber" capital-gains chatbot (realized
  // STCG/LTCG + loss-harvest / LT-crossover / ₹1.25L levers) has BOTH a page and a
  // FAB on /stocks.
  { path: 'stocks',            component: Equity },
  { path: 'stocks-manual',     component: StockAccounts },
  { path: 'stock-tax',         component: StockTax },
  // Aionion broker .xlsx import — equities, MFs and bonds into the portfolio.
  { path: 'aionion-import',    component: AionionImport },
  // F&O — live Zerodha derivatives: strategy P&L (Sentinel straddle, Crude),
  // daily P&L calendar, per-second intraday chart & multi-account Kite login
  { path: 'fno',               component: Fno },
  // Bonds — per-bond income, YTM, payout calendar & maturity ladder.
  // Bond tax ("Bandhan": coupon-income tax, TDS/15G, whose-name split) has BOTH a page
  // and a FAB on /bonds.
  { path: 'bonds',             component: Bonds },
  { path: 'bonds-tax',         component: BondsTax },
  // Salary — per-person earned income (multi-currency → live ₹) feeding the dashboard
  { path: 'salary',            component: Salary },
  // Other Income — dividends, interest, rent, bonuses, gifts: month-by-month log + reminders
  { path: 'other-income',      component: OtherIncome },
  // Loans — liabilities: outstanding, EMI, progress; EMIs feed the reminders timeline
  { path: 'loans',             component: Loans },
  // Reminders — unified date-wise timeline of money in & out (bonds, FDs, loans, income, expenses)
  { path: 'reminders',         component: Reminders },
  // ULIP — unit-linked insurance: fund value, premiums, lock-in/maturity & XIRR
  { path: 'ulip',              component: Ulip },
  // LIC — traditional life insurance: cover, premiums, maturity & bonus ladder
  { path: 'lic',               component: Lic },
  // Fixed Deposits — value + payout interest income, maturity & rate
  { path: 'fd',                component: Fd },
  // Monthly expenses — recurring spend (multi-currency) → surplus & savings rate
  { path: 'expenses',          component: Expenses },
  // Cash & funds in hand — bank + physical cash, most liquid
  { path: 'cash',              component: Cash },
  // Documents — secure vault: nested folders + files, plus linked land/apt/gold docs
  { path: 'documents',         component: Documents },
  // Saved Strategies — list of all stocks you've invested in (CC + future strategies)
  { path: 'saved-strategies',  component: SavedStrategiesComponent },
  // Strategies Hub (catalogue of available strategies) kept at /strategies
  { path: 'strategies',        component: StrategiesHubComponent },

  // Dashboard moved off the root
  { path: 'dashboard',         component: DashboardComponent },

  { path: 'scanner',           component: ScannerComponent },
  { path: 'covered-call',      component: CoveredCallComponent },
  { path: 'protected-wheel',   component: ProtectedWheelComponent },
  { path: 'short-strangle',    component: ShortStrangleComponent },
  { path: 'hedges',            component: HedgesComponent },
  { path: 'cc-simulator',      component: CcSimulatorComponent },
  { path: 'strategy-math',     component: StrategyMathComponent },
  { path: 'math-handbook',     component: MathHandbookComponent },
  { path: 'portfolio',         component: PortfolioComponent },
  { path: 'smart-money',       component: SmartMoneyComponent },
  { path: 'scorecard',         component: ScorecardComponent },
  { path: 'exit-strategy',     component: ExitStrategyComponent },
  { path: 'pe-scanner',        component: PeScannerComponent },
  { path: 'strategy',          component: StrategyComponent },
  { path: 'settings',          component: SettingsComponent },
  { path: '**',                redirectTo: '' },
];
