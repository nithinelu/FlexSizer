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

Named User (NU) buying model, for comparison: every user/device is licensed 1:1 in its
own tier (Professional A-FLEX-NUPL-P, Enhanced A-FLEX-NUPL-E, Access/Common Area
A-FLEX-NUPL-A). There is no minimum, no growth allowance and no free Common Area or
Access entitlement. The same user-type mapping is used: Professional -> NU Professional,
Enhanced (Task Worker / Common Area) -> NU Enhanced, Access -> NU Access.
"""

import math
import re
from dataclasses import dataclass, field

DATA_SHEET_URL = ("https://www.cisco.com/c/en/us/products/collateral/unified-communications/"
                  "cisco-collaboration-flex-plan/collaboration-flex-plan3-data-sheet.html")

LICENSE_TYPES = ["UWL Standard", "UCL Enhanced Plus", "UCL Enhanced", "UCL Essential", "UCL Basic"]
TREATMENTS = {"KW": "Professional / KW: EA licence", "CA": "Enhanced / Common Area (free 50%)",
              "ACCESS": "Access / Analog (free 20%)", "FREE": "Not counted"}

DEFAULT_SETTINGS = {
    "treatment": {"UWL Standard": "KW", "UCL Enhanced Plus": "KW", "UCL Enhanced": "CA",
                  "UCL Essential": "ACCESS", "UCL Basic": "ACCESS"},
    "ca_pct": 50.0,          # Common Area devices included, % of KW
    "access_pct": 20.0,      # Access licences included, % of KW
    "min_ea": 250,           # EA minimum KW quantity
    "growth_allowance_pct": 20.0,   # included growth allowance (information only)
    "premises_reg_pct": 20.0,       # premises device registration included (information only)
    "headroom_pct": 0.0,     # planning head-room added to every quantity before sizing
    "excess_mode": "addon",  # how many EA licences to buy - see EA_MODES
    "ea_manual": 0,          # EA quantity when excess_mode is "manual"
    # Monthly list prices per licence (CCW, Oct 2026) and subscription term
    "price_kw": 11.75,       # A-FLEX-EAPL      EntW On-Premises Calling
    "price_ca": 5.75,        # A-FLEX-PL-COMMON On-Premises Common Area Add-on
    "price_access": 3.95,    # A-FLEX-PL-ACCESS On-Premises Access Add-on
    "discount_pct": 0.0,     # discount off list
    "term_months": 24,
    # Named User monthly list prices (CCW, Oct/Nov 2026). A price of 0 means "not entered"
    # and the comparison warns instead of giving a verdict.
    "price_nu_pro": 10.95,   # A-FLEX-NUPL-P NU On-Premises Calling Professional
    "price_nu_enh": 7.25,    # A-FLEX-NUPL-E NU On-Premises Calling Enhanced
    "price_nu_access": 3.95,  # A-FLEX-NUPL-A NU On-Premises Calling Access
    "nu_discount_pct": 0.0,
}

EA_MODES = {
    "addon": "Cheapest: KWs + add-ons for the rest",
    "match": "Match free allowance: no add-ons",
    "ea": "Fewest EA licences with no add-ons",
    "manual": "Manual EA quantity",
}

SKUS = {"kw": "A-FLEX-EAPL", "ca": "A-FLEX-PL-COMMON", "access": "A-FLEX-PL-ACCESS",
        "nu_pro": "A-FLEX-NUPL-P", "nu_enh": "A-FLEX-NUPL-E", "nu_access": "A-FLEX-NUPL-A"}
NU_TIER = {"KW": "nu_pro", "CA": "nu_enh", "ACCESS": "nu_access"}   # EA treatment -> NU tier


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
    manual_raised: bool = False    # manual EA quantity was below the KW count / EA minimum
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
    mode = s["excess_mode"]
    if mode == "manual":
        want = max(0, int(s["ea_manual"] or 0))
        r.manual_raised = 0 < want < n
        n = max(n, want)
    elif mode == "match":
        # Enough EA licences that the free 50% / 20% alone covers every Common Area and Access device.
        for devices, pct in ((ca, ca_pct), (ac, access_pct)):
            if devices and pct:
                n = max(n, math.ceil(devices * 100.0 / pct - 1e-9))
                while _pct_of(n, pct) < devices:
                    n += 1
    elif mode == "ea":
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
    for mode, label in (("ea", "Fewest KW, no add-ons"), ("match", "Match free allowance")):
        n = calculate(quantities, {**s, "excess_mode": mode}).ea_licenses
        if n not in seen:
            seen.add(n)
            out.append((label, calculate(quantities, {**s, "ea_override": n})))
    return out


def marginal_costs(settings):
    """Monthly cost of one more device/user once the included allowance is used up."""
    s = {**DEFAULT_SETTINGS, **settings}
    p_kw, p_ca, p_ac = unit_prices(s)
    # A new KW also brings ca_pct/access_pct of free devices, which only saves
    # money while add-ons are being bought.
    kw_net = p_kw - p_ca * float(s["ca_pct"]) / 100.0 - p_ac * float(s["access_pct"]) / 100.0
    return {"ca": p_ca, "access": p_ac, "kw": p_kw, "kw_net": kw_net}


# ---------------------------------------------------------------- phone models -> UCL licence
# From Cisco's UCL descriptions: Basic = one device incl. all Essential devices, basic call
# control (6911, 6921, 9811); Enhanced = one device incl. all Basic devices, advanced call
# control incl. desktop/mobile clients (3911, 3951, 6941/45/61, 79xx, 89xx, 9841/51/61/71,
# 99xx, E20, EX60, EX90, third-party SIP). Essential = one basic voice/analog device
# (analog phone/fax, ATA 186/187, 3905, 6901). Enhanced Plus = up to 2 devices, CUWL = up
# to 10 devices per user. TelePresence Room endpoints are included in Flex (no licence to
# buy); CTI route points/ports are covered by EA KW licences, else need UCL Enhanced.
ROOM = "TelePresence Room"
CTI = "CTI route point / port"
PHONE_MODELS = {
    "Analog phone / fax port": "UCL Essential",
    "Cisco ATA 186/187/190/191": "UCL Essential",
    "Cisco 3905": "UCL Essential",
    "Cisco 6901": "UCL Essential",
    "Cisco 6911": "UCL Basic",
    "Cisco 6921": "UCL Basic",
    "Cisco 9811": "UCL Basic",
    "Cisco 3911": "UCL Enhanced",
    "Cisco 3951": "UCL Enhanced",
    "Cisco 6941": "UCL Enhanced",
    "Cisco 6945": "UCL Enhanced",
    "Cisco 6961": "UCL Enhanced",
    "Cisco 79xx": "UCL Enhanced",
    "Cisco 89xx": "UCL Enhanced",
    "Cisco 9841": "UCL Enhanced",
    "Cisco 9851": "UCL Enhanced",
    "Cisco 9861": "UCL Enhanced",
    "Cisco 9871": "UCL Enhanced",
    "Cisco 99xx": "UCL Enhanced",
    "Cisco E20": "UCL Enhanced",
    "Cisco TelePresence EX60": "UCL Enhanced",
    "Cisco TelePresence EX90": "UCL Enhanced",
    "Third-party SIP phone": "UCL Enhanced",
    "Cisco TelePresence SX / MX / TX / IX": ROOM,
    "Cisco TelePresence System 3200/3000/1300 / Profile": ROOM,
    "Webex Room / Room Kit (Spark Room)": ROOM,
    CTI: CTI,
}
# Soft clients a user runs next to their phone(s) -> extra devices.
SOFT_CLIENTS = {"None": 0, "1 (Jabber or Webex)": 1, "2 (Jabber and Webex)": 2, "3 or more": 3}
NO_SECOND = "(none)"
DEFAULT_MODEL_LICENCE = "UCL Enhanced"   # Enhanced covers any single device


def licence_for_devices(models, soft_clients):
    """Licence for one user: 1 device -> the phone's own UCL, 2 -> Enhanced Plus, 3-10 -> UWL Standard (CUWL)."""
    devices = len(models) + soft_clients
    if devices >= 3:
        return "UWL Standard", f"{devices} devices per user (CUWL: up to 10)"
    if devices == 2:
        return "UCL Enhanced Plus", "2 devices per user"
    lic, known = licence_for_model(models[0])
    return lic, "Cisco UCL table" if known else "not in list - Enhanced assumed"


def licence_for_model(model):
    """(licence, known) for a phone model name or number such as '7942', 'CP-9811' or 'Cisco 79xx'."""
    if model in PHONE_MODELS:
        return PHONE_MODELS[model], True
    text = model.upper()
    if "CTI" in text:
        return CTI, True
    if "ANALOG" in text or "ATA" in text or "FAX" in text or text.strip().startswith("VG"):
        return "UCL Essential", True
    groups = re.findall(r"\d{4}", text)   # model number in names and part numbers (DP-9841-K9)
    digits = groups[0] if groups else ""
    if (re.search(r"\b(?:CTS-|TTC\d-)?(SX|MX|TX|IX)\d", text) or "ROOM" in text or "PROFILE" in text
            or "BOARD" in text or digits in ("3200", "3000", "1300")):
        return ROOM, True
    for name, lic in PHONE_MODELS.items():
        key = name.split()[-1].upper()
        if key.endswith("XX") and len(digits) == 4 and digits[:2] == key[:2]:
            return lic, True
        if key == digits or key == text.strip() or (key.isalnum() and key in text.split()):
            return lic, True
    if "SIP" in text:
        return "UCL Enhanced", True
    return DEFAULT_MODEL_LICENCE, False


def suggest_licences(rows, cti_as_enhanced=False):
    """rows: [(model, 2nd model or '', users, soft clients label, licence override or '')].

    Each row is `users` people, each with one or two phones plus soft clients.
    Returns ({licence: qty}, {ROOM: qty, CTI: qty}, [(models text, qty, soft, licence, why)]).
    Room endpoints are never licence rows; CTI ports become UCL Enhanced only when asked
    (Named User agreements have no KW licences to cover them).
    """
    totals = {lic: 0 for lic in LICENSE_TYPES}
    extras = {ROOM: 0, CTI: 0}
    detail = []
    for model, model2, qty, soft, override in rows:
        models = [m for m in (model, model2) if m and m != NO_SECOND]
        model_lic, _ = licence_for_model(model)
        if override:
            lic, why = override, "set by you"
        elif model_lic in (ROOM, CTI):
            lic = model_lic
            why = "included in Flex" if lic == ROOM else (
                "counted as UCL Enhanced" if cti_as_enhanced else "covered by EA KW licences")
        else:
            lic, why = licence_for_devices(models, SOFT_CLIENTS.get(soft, 0))
        model = " + ".join(models)
        if lic == CTI and cti_as_enhanced:
            totals["UCL Enhanced"] += qty
        elif lic in extras:
            extras[lic] += qty
        else:
            totals[lic] += qty
        detail.append((model, qty, soft, lic, why))
    return totals, extras, detail


@dataclass
class NUResult:
    qty: dict = field(default_factory=dict)       # NU tier -> licences
    unit: dict = field(default_factory=dict)      # NU tier -> monthly price after discount
    cost: dict = field(default_factory=dict)      # NU tier -> monthly cost
    monthly: float = 0.0
    term_total: float = 0.0
    missing_price: list = field(default_factory=list)   # tiers with users but no price entered


def calculate_nu(r, settings):
    """Named User cost for the quantities already sized in EA result r."""
    s = {**DEFAULT_SETTINGS, **settings}
    f = 1 - max(0.0, min(100.0, float(s["nu_discount_pct"]))) / 100.0
    nu = NUResult()
    for tier in NU_TIER.values():
        nu.qty[tier] = 0
        nu.unit[tier] = float(s["price_" + tier]) * f
    for _, _, sized, how in r.lines:
        if how in NU_TIER:
            nu.qty[NU_TIER[how]] += sized
    for tier, q in nu.qty.items():
        nu.cost[tier] = q * nu.unit[tier]
        if q and not nu.unit[tier]:
            nu.missing_price.append(SKUS[tier])
    nu.monthly = sum(nu.cost.values())
    nu.term_total = nu.monthly * max(1, int(s["term_months"]))
    return nu


def compare(r, nu):
    """(cheaper model, term saving, saving %) - 'EA', 'NU' or 'Same'."""
    diff = r.term_total - nu.term_total
    if abs(diff) < 0.005:
        return "Same", 0.0, 0.0
    high = max(r.term_total, nu.term_total)
    return ("NU" if diff > 0 else "EA"), abs(diff), abs(diff) / high * 100 if high else 0.0


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
        f"EA quantity: {EA_MODES.get(s['excess_mode'], s['excess_mode'])}"
        + (f" ({s['ea_manual']})" if s["excess_mode"] == "manual" else ""),
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
    ]
    nu = calculate_nu(r, s)
    out.append(f"Named User alternative ({term}-month term, {s['nu_discount_pct']:g}% discount)")
    for tier in NU_TIER.values():
        out.append(f"  {SKUS[tier]:<17}{nu.qty[tier]:>16,}{nu.unit[tier]:>10,.2f}{nu.cost[tier]:>12,.2f}"
                   f"{nu.cost[tier] * term:>12,.2f}")
    out.append(f"  {'TOTAL':<43}{nu.monthly:>12,.2f}{nu.term_total:>12,.2f}")
    winner, saving, pct = compare(r, nu)
    if nu.missing_price:
        out.append(f"  ! No price entered for {', '.join(nu.missing_price)} - NU total is incomplete")
    elif winner == "Same":
        out.append("  EA and Named User cost the same")
    else:
        out.append(f"  CHEAPER: {'Enterprise Agreement' if winner == 'EA' else 'Named User'} "
                   f"saves {saving:,.2f} over {term} months ({pct:.1f}%)")
    out += ["", f"Source: Cisco Collaboration Flex Plan 3.0 data sheet - {DATA_SHEET_URL}"]
    return "\n".join(out)
