"""Cisco Collaboration Flex Plan 3.0 Enterprise Agreement (EA) sizing engine (no UI).

Copyright (c) 2026 Nithin Eluvathingal. MIT License - see LICENSE.

Rules from the Flex Plan 3.0 data sheet (on-premises calling, EA buying model):
  * The EA covers all Knowledge Workers (KWs); minimum 250 KWs.
  * Common Area devices are included at 50% of the KW count.
  * Access licences are included at 20% of the KW count.
  * A 15% growth allowance is included for new Flex 3.0 subscriptions
    (CCW actually deposits A-FLEX-P-EA smart licences at 120% of the KW qty,
    so the default here is 20%).
  * Premises device registration is included for up to 20% of KWs.
  * Common Area and Access devices above the included amount need add-on licences.

Existing perpetual licences map to Flex user types (Cisco migration table):
  UWL Standard, UCL Enhanced Plus -> Professional (Knowledge Worker)      -> 1 EA licence each
  UCL Enhanced                    -> Enhanced (Task Worker / Common Area) -> 50% free pool
  UCL Essential, UCL Basic        -> Access (Analog)                      -> 20% free pool
Each mapping can be changed per licence type.
"""

import math
from dataclasses import dataclass, field

DATA_SHEET_URL = ("https://www.cisco.com/c/en/us/products/collateral/unified-communications/"
                  "cisco-collaboration-flex-plan/collaboration-flex-plan3-data-sheet.html")

LICENSE_TYPES = ["UWL Standard", "UCL Enhanced Plus", "UCL Enhanced", "UCL Essential", "UCL Basic"]
TREATMENTS = {"KW": "Professional (Knowledge Worker)", "CA": "Enhanced (Task Worker / Common Area)",
              "ACCESS": "Access (Analog)", "FREE": "Not counted"}

DEFAULT_SETTINGS = {
    "treatment": {"UWL Standard": "KW", "UCL Enhanced Plus": "KW", "UCL Enhanced": "CA",
                  "UCL Essential": "ACCESS", "UCL Basic": "ACCESS"},
    "ca_pct": 50.0,          # Common Area devices included, % of KW
    "access_pct": 20.0,      # Access licences included, % of KW
    "min_ea": 250,           # EA minimum KW quantity
    "growth_allowance_pct": 20.0,   # included growth allowance (information only)
    "premises_reg_pct": 20.0,       # premises device registration included (information only)
    "headroom_pct": 0.0,     # planning head-room added to every quantity before sizing
    "excess_mode": "addon",  # "addon" = Common Area/Access add-ons, "ea" = buy extra KWs instead
    # Monthly list prices per licence (CCW, Oct 2026) and subscription term
    "price_kw": 11.75,       # A-FLEX-EAPL      EntW On-Premises Calling
    "price_ca": 5.75,        # A-FLEX-PL-COMMON On-Premises Common Area Add-on
    "price_access": 3.95,    # A-FLEX-PL-ACCESS On-Premises Access Add-on
    "discount_pct": 0.0,     # discount off list
    "term_months": 24,
}

SKUS = {"kw": "A-FLEX-EAPL", "ca": "A-FLEX-PL-COMMON", "access": "A-FLEX-PL-ACCESS"}


@dataclass
class Result:
    knowledge_workers: int = 0     # KW-treated quantities (after head-room)
    ca_devices: int = 0
    access_devices: int = 0
    not_counted: int = 0
    ea_licenses: int = 0           # Flex EA KW licences to order
    ca_included: int = 0           # free Common Area from the final EA qty
    access_included: int = 0       # free Access from the final EA qty
    spare_kw: int = 0              # EA licences above the KW count (minimum / folded excess)
    ca_addon: int = 0              # Common Area add-ons to order
    access_addon: int = 0          # Access add-ons to order
    ca_unused: int = 0
    access_unused: int = 0
    growth_ceiling: int = 0        # KW count reachable inside the growth allowance
    premises_reg: int = 0          # included premises device registrations
    min_applied: bool = False
    lines: list = field(default_factory=list)   # (type, qty, sized qty, treatment)
    cost_kw: float = 0.0           # monthly, after discount
    cost_ca: float = 0.0
    cost_access: float = 0.0
    monthly: float = 0.0
    term_total: float = 0.0


def _pct_of(n, pct):
    return math.floor(n * pct / 100.0 + 1e-9)


def _addons(n, kw, ca, access, ca_pct, access_pct):
    """Common Area and Access add-ons needed when n EA licences are bought.

    EA licences above the KW count can carry any device, so they absorb
    Access excess first, then Common Area excess.
    """
    ca_ex = max(0, ca - _pct_of(n, ca_pct))
    ac_ex = max(0, access - _pct_of(n, access_pct))
    spare = n - kw
    used = min(spare, ac_ex)
    ac_ex -= used
    ca_ex = max(0, ca_ex - (spare - used))
    return ca_ex, ac_ex


def calculate(quantities, settings):
    """quantities: {licence type: int}; settings: dict like DEFAULT_SETTINGS."""
    s = {**DEFAULT_SETTINGS, **settings}
    ca_pct = max(0.0, float(s["ca_pct"]))
    access_pct = max(0.0, float(s["access_pct"]))
    headroom = max(0.0, float(s["headroom_pct"]))
    min_ea = max(0, int(s["min_ea"]))
    treatment = s.get("treatment", {})

    r = Result()
    for lic in LICENSE_TYPES:
        qty = max(0, int(quantities.get(lic, 0) or 0))
        sized = math.ceil(qty * (1 + headroom / 100.0)) if qty else 0
        how = treatment.get(lic, DEFAULT_SETTINGS["treatment"][lic])
        r.lines.append((lic, qty, sized, how))
        if how == "KW":
            r.knowledge_workers += sized
        elif how == "CA":
            r.ca_devices += sized
        elif how == "ACCESS":
            r.access_devices += sized
        else:
            r.not_counted += sized

    kw, ca, ac = r.knowledge_workers, r.ca_devices, r.access_devices
    n = max(kw, min_ea, int(s.get("ea_override") or 0))
    if s["excess_mode"] == "ea":
        # Smallest n where every device is covered by KWs + included allowances.
        # Uncovered devices fall as n rises, so "covered(n)" is monotone: binary search.
        def covered(m):
            return _addons(m, kw, ca, ac, ca_pct, access_pct) == (0, 0)
        lo, hi = n, max(n, kw + ca + ac)
        while lo < hi:
            mid = (lo + hi) // 2
            if covered(mid):
                hi = mid
            else:
                lo = mid + 1
        n = lo

    r.ea_licenses = n
    r.min_applied = min_ea > kw and n == min_ea
    r.spare_kw = n - kw
    r.ca_included = _pct_of(n, ca_pct)
    r.access_included = _pct_of(n, access_pct)
    r.ca_addon, r.access_addon = _addons(n, kw, ca, ac, ca_pct, access_pct)
    r.ca_unused = max(0, r.ca_included - ca)
    r.access_unused = max(0, r.access_included - ac)
    r.growth_ceiling = n + _pct_of(n, float(s["growth_allowance_pct"]))
    r.premises_reg = _pct_of(n, float(s["premises_reg_pct"]))
    _price(r, s)
    return r


def unit_prices(settings):
    """Monthly price per KW, Common Area add-on and Access add-on after discount."""
    s = {**DEFAULT_SETTINGS, **settings}
    f = 1 - max(0.0, min(100.0, float(s["discount_pct"]))) / 100.0
    return float(s["price_kw"]) * f, float(s["price_ca"]) * f, float(s["price_access"]) * f


def _price(r, s):
    p_kw, p_ca, p_ac = unit_prices(s)
    r.cost_kw = r.ea_licenses * p_kw
    r.cost_ca = r.ca_addon * p_ca
    r.cost_access = r.access_addon * p_ac
    r.monthly = r.cost_kw + r.cost_ca + r.cost_access
    r.term_total = r.monthly * max(1, int(s["term_months"]))


def kw_scenarios(quantities, settings, extra_steps=(0, 10, 25, 50, 100, 250, 500)):
    """Cost of buying more EA KW licences than needed (instead of add-ons).

    Returns [(label, Result)] - the minimum-cost option first, then the minimum
    plus each step, then the point where KWs alone cover every device.
    """
    s = {**DEFAULT_SETTINGS, **settings, "excess_mode": "addon"}
    base = calculate(quantities, s)
    out, seen = [], set()
    for step in extra_steps:
        n = base.ea_licenses + step
        if n not in seen:
            seen.add(n)
            out.append(("Recommended" if step == 0 else f"+{step} KW",
                        calculate(quantities, {**s, "ea_override": n})))
    full = calculate(quantities, {**s, "excess_mode": "ea"})
    if full.ea_licenses not in seen:
        out.append(("KW covers all devices", calculate(quantities, {**s, "ea_override": full.ea_licenses})))
    return out


def marginal_costs(settings):
    """Monthly cost of one more device/user once the included allowance is used up."""
    s = {**DEFAULT_SETTINGS, **settings}
    p_kw, p_ca, p_ac = unit_prices(s)
    # A new KW also brings ca_pct/access_pct of free devices, which only saves
    # money while add-ons are being bought.
    kw_net = p_kw - p_ca * float(s["ca_pct"]) / 100.0 - p_ac * float(s["access_pct"]) / 100.0
    return {"ca": p_ca, "access": p_ac, "kw": p_kw, "kw_net": kw_net}


def summary_text(r, settings):
    s = {**DEFAULT_SETTINGS, **settings}
    out = ["FlexSizer - Cisco Flex 3.0 EA licence sizing (on-premises calling)", ""]
    out.append(f"{'Licence type':<19}{'Qty':>7}{'Sized':>8}  Flex user type")
    for lic, qty, sized, how in r.lines:
        out.append(f"{lic:<19}{qty:>7}{sized:>8}  {TREATMENTS.get(how, how)}")
    out += [
        "",
        f"Rules: Common Area {s['ca_pct']:g}% of KW, Access {s['access_pct']:g}% of KW, "
        f"minimum {s['min_ea']} KW, planning head-room {s['headroom_pct']:g}%",
        "",
        f"Professional (Knowledge Worker):  {r.knowledge_workers}",
        f"Enhanced (Task Worker/Common Area): {r.ca_devices}  (included {r.ca_included}, unused {r.ca_unused})",
        f"Access (Analog):                  {r.access_devices}  (included {r.access_included}, unused {r.access_unused})",
        "",
        f"FLEX EA KNOWLEDGE WORKER LICENCES: {r.ea_licenses}"
        + ("   (EA minimum applied)" if r.min_applied else ""),
    ]
    if r.ca_addon:
        out.append(f"COMMON AREA ADD-ON LICENCES:       {r.ca_addon}")
    if r.access_addon:
        out.append(f"ACCESS ADD-ON LICENCES:            {r.access_addon}")
    p_kw, p_ca, p_ac = unit_prices(s)
    term = int(s["term_months"])
    out += [
        "",
        f"Cost ({term}-month term, {s['discount_pct']:g}% discount)   Qty   Unit/mo     Monthly        Term",
        f"  {SKUS['kw']:<17}{r.ea_licenses:>16,}{p_kw:>10,.2f}{r.cost_kw:>12,.2f}{r.cost_kw * term:>12,.2f}",
        f"  {SKUS['ca']:<17}{r.ca_addon:>16,}{p_ca:>10,.2f}{r.cost_ca:>12,.2f}{r.cost_ca * term:>12,.2f}",
        f"  {SKUS['access']:<17}{r.access_addon:>16,}{p_ac:>10,.2f}{r.cost_access:>12,.2f}"
        f"{r.cost_access * term:>12,.2f}",
        f"  {'TOTAL':<43}{r.monthly:>12,.2f}{r.term_total:>12,.2f}",
        "",
        f"Growth allowance ({s['growth_allowance_pct']:g}%): up to {r.growth_ceiling} KWs without true-forward",
        f"Premises device registration included: {r.premises_reg}",
        "",
        f"Source: Cisco Collaboration Flex Plan 3.0 data sheet - {DATA_SHEET_URL}",
    ]
    return "\n".join(out)
