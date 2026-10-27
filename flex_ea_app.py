"""Cisco Flex EA Licence Calculator - Tkinter front end for flex_ea_calc.

Copyright (c) 2026 Nithin Eluvathingal. MIT License - see LICENSE.
"""

import copy
import json
import os
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from flex_ea_calc import (DATA_SHEET_URL, DEFAULT_SETTINGS, LICENSE_TYPES, SKUS, TREATMENTS,
                          calculate, kw_scenarios, marginal_costs, summary_text, unit_prices)

APP_NAME = "Cisco Flex EA Calculator"
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")
SETTINGS_VERSION = 4   # v2: data sheet rules; v3: pricing, 20% growth; v4: Cisco user-type mapping. Older saves are ignored
TREATMENT_BY_LABEL = {v: k for k, v in TREATMENTS.items()}
RULE_FIELDS = [   # (settings key, label, number type)
    ("ca_pct", "Common Area included (% of KW)", float),
    ("access_pct", "Access included (% of KW)", float),
    ("min_ea", "EA minimum (KW)", int),
    ("growth_allowance_pct", "Growth allowance (%)", float),
    ("premises_reg_pct", "Premises device registration (% of KW)", float),
    ("headroom_pct", "Planning head-room on quantities (%)", float),
]
PRICE_FIELDS = [
    ("price_kw", f"EA Knowledge Worker  {SKUS['kw']}", float),
    ("price_ca", f"Common Area add-on  {SKUS['ca']}", float),
    ("price_access", f"Access add-on  {SKUS['access']}", float),
    ("discount_pct", "Discount off list (%)", float),
    ("term_months", "Term (months)", int),
]
WHATIF_FIELDS = [("kw", "Extra knowledge workers"), ("ca", "Extra Common Area devices"),
                 ("access", "Extra Access devices")]


def money(v):
    return f"{v:,.2f}"


def load_settings():
    s = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("version") == SETTINGS_VERSION:
            treatment = saved.pop("treatment", {})
            s["treatment"].update({k: v for k, v in treatment.items() if v in TREATMENTS})
            s.update({k: v for k, v in saved.items() if k in s})
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({**s, "version": SETTINGS_VERSION}, f, indent=2)


def to_number(text, kind=int):
    try:
        return max(0, kind(str(text).strip() or 0))
    except ValueError:
        return 0


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=14, style="App.TFrame")
        self.settings = load_settings()
        self.qty_vars = {lic: tk.StringVar(value="0") for lic in LICENSE_TYPES}
        self.how_vars = {lic: tk.StringVar(value=TREATMENTS[self.settings["treatment"][lic]])
                         for lic in LICENSE_TYPES}
        self.rule_vars = {key: tk.StringVar(value=f"{self.settings[key]:g}")
                          for key, _, _ in RULE_FIELDS + PRICE_FIELDS}
        self.excess_mode = tk.StringVar(value=self.settings["excess_mode"])
        self.whatif_vars = {key: tk.StringVar(value="0") for key, _ in WHATIF_FIELDS}
        self.result = None
        self._build()
        for v in [*self.qty_vars.values(), *self.how_vars.values(), *self.rule_vars.values(),
                  *self.whatif_vars.values(), self.excess_mode]:
            v.trace_add("write", lambda *_: self.recalc())
        self.recalc()

    # ---------------------------------------------------------------- layout
    def _build(self):
        self.columnconfigure(1, weight=1)
        bold = ("Segoe UI", 9, "bold")

        inp = ttk.LabelFrame(self, text="Existing perpetual licences", padding=10)
        inp.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(inp, text="Licence type", font=bold).grid(row=0, column=0, sticky="w")
        ttk.Label(inp, text="Quantity", font=bold).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(inp, text="Flex user type", font=bold).grid(row=0, column=2, sticky="w")
        for i, lic in enumerate(LICENSE_TYPES, start=1):
            ttk.Label(inp, text=lic).grid(row=i, column=0, sticky="w", pady=3)
            e = ttk.Spinbox(inp, from_=0, to=1_000_000, width=10, textvariable=self.qty_vars[lic])
            e.grid(row=i, column=1, padx=8, pady=3)
            if i == 1:
                self.first_entry = e
            ttk.Combobox(inp, values=list(TREATMENTS.values()), state="readonly", width=34,
                         textvariable=self.how_vars[lic]).grid(row=i, column=2, pady=3)
        ttk.Button(inp, text="Clear quantities", command=self.clear).grid(
            row=len(LICENSE_TYPES) + 1, column=0, columnspan=3, sticky="w", pady=(8, 0))

        opt = ttk.LabelFrame(self, text="Flex 3.0 EA rules (from Cisco data sheet)", padding=10)
        opt.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        for i, (key, label, _) in enumerate(RULE_FIELDS):
            ttk.Label(opt, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(opt, width=10, textvariable=self.rule_vars[key]).grid(row=i, column=1, sticky="w", padx=8)
        row = len(RULE_FIELDS)
        ttk.Label(opt, text="Devices above the included amount").grid(row=row, column=0, sticky="nw", pady=(6, 0))
        rb = ttk.Frame(opt)
        rb.grid(row=row, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Radiobutton(rb, text="Common Area / Access add-ons", value="addon",
                        variable=self.excess_mode).pack(anchor="w")
        ttk.Radiobutton(rb, text="Buy extra EA KW licences", value="ea",
                        variable=self.excess_mode).pack(anchor="w")
        btns = ttk.Frame(opt)
        btns.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(btns, text="Save rules as default", command=self.save_defaults).pack(side="left")
        ttk.Button(btns, text="Reset to data sheet", command=self.reset_rules).pack(side="left", padx=8)
        link = ttk.Label(opt, text="Open Cisco Flex 3.0 data sheet", foreground="#1F5FA8", cursor="hand2")
        link.grid(row=row + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        link.bind("<Button-1>", lambda _: webbrowser.open(DATA_SHEET_URL))
        ttk.Label(opt, text="© 2026 Nithin Eluvathingal · MIT License", foreground="#888").grid(
            row=row + 3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        price = ttk.LabelFrame(self, text="Pricing (per licence per month, USD list)", padding=10)
        price.grid(row=2, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        for i, (key, label, _) in enumerate(PRICE_FIELDS):
            ttk.Label(price, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(price, width=10, textvariable=self.rule_vars[key]).grid(row=i, column=1, sticky="w", padx=8)

        res = ttk.LabelFrame(self, text="Result", padding=10)
        res.grid(row=0, column=1, rowspan=3, sticky="nsew")
        self.rowconfigure(2, weight=1)
        res.columnconfigure(0, weight=1)
        res.rowconfigure(2, weight=1)
        big = ttk.Frame(res)
        big.grid(row=0, column=0, sticky="ew")
        self.ea_label = ttk.Label(big, text="0", font=("Segoe UI", 28, "bold"), foreground="#1F5FA8")
        self.ea_label.grid(row=0, column=0, sticky="w")
        ttk.Label(big, text="Flex EA Knowledge Worker licences", font=("Segoe UI", 11)).grid(
            row=0, column=1, sticky="w", padx=10)
        self.addon_label = ttk.Label(big, text="", font=("Segoe UI", 11, "bold"), foreground="#B5401B")
        self.addon_label.grid(row=1, column=0, columnspan=2, sticky="w")
        self.note_label = ttk.Label(res, text="", foreground="#666", wraplength=420, justify="left")
        self.note_label.grid(row=1, column=0, sticky="w", pady=(2, 8))

        tabs = ttk.Notebook(res)
        tabs.grid(row=2, column=0, sticky="nsew")

        self.tree = ttk.Treeview(tabs, columns=("value",), show="tree", height=16)
        self.tree.column("#0", width=320)
        self.tree.column("value", width=120, anchor="e")
        tabs.add(self.tree, text="Summary & cost")

        cols = ("kw", "ca", "access", "monthly", "term", "delta")
        heads = ("EA KW", "CA add-on", "Access add-on", "Monthly", "Term total", "vs recommended")
        sc = ttk.Frame(tabs, padding=6)
        self.scen = ttk.Treeview(sc, columns=cols, show="tree headings", height=9)
        self.scen.heading("#0", text="Option")
        self.scen.column("#0", width=150)
        for c, h in zip(cols, heads):
            self.scen.heading(c, text=h)
            self.scen.column(c, width=90, anchor="e")
        self.scen.pack(fill="both", expand=True)
        self.scen_note = ttk.Label(sc, text="", foreground="#666", wraplength=640, justify="left")
        self.scen_note.pack(anchor="w", pady=(6, 0))
        tabs.add(sc, text="More KW vs add-ons")

        wi = ttk.Frame(tabs, padding=6)
        for i, (key, label) in enumerate(WHATIF_FIELDS):
            ttk.Label(wi, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Spinbox(wi, from_=0, to=1_000_000, width=10, textvariable=self.whatif_vars[key]).grid(
                row=i, column=1, sticky="w", padx=8)
        cols = ("now", "after", "delta")
        self.whatif = ttk.Treeview(wi, columns=cols, show="tree headings", height=8)
        self.whatif.heading("#0", text="")
        self.whatif.column("#0", width=200)
        for c, h in zip(cols, ("Current", "After change", "Difference")):
            self.whatif.heading(c, text=h)
            self.whatif.column(c, width=110, anchor="e")
        self.whatif.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self.unit_note = ttk.Label(wi, text="", foreground="#666", wraplength=640, justify="left")
        self.unit_note.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        wi.columnconfigure(2, weight=1)
        wi.rowconfigure(3, weight=1)
        tabs.add(wi, text="What-if: add devices")

        out = ttk.Frame(res)
        out.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Button(out, text="Copy summary", command=self.copy_summary).pack(side="left")
        ttk.Button(out, text="Export to Excel…", command=self.export_excel).pack(side="left", padx=8)

    # ---------------------------------------------------------------- logic
    def current_settings(self):
        s = {key: to_number(self.rule_vars[key].get(), kind) for key, _, kind in RULE_FIELDS + PRICE_FIELDS}
        s["treatment"] = {lic: TREATMENT_BY_LABEL[self.how_vars[lic].get()] for lic in LICENSE_TYPES}
        s["excess_mode"] = self.excess_mode.get()
        return s

    def result_rows(self, r, s):
        return [
            ("Professional (Knowledge Workers)", r.knowledge_workers),
            ("Flex EA Knowledge Worker licences", r.ea_licenses),
            ("Enhanced (Task Worker / Common Area)", r.ca_devices),
            (f"   Included ({s['ca_pct']:g}% of {r.ea_licenses:,})", r.ca_included),
            ("   Common Area add-on licences", r.ca_addon),
            ("   Unused Common Area allowance", r.ca_unused),
            ("Access (Analog)", r.access_devices),
            (f"   Included ({s['access_pct']:g}% of {r.ea_licenses:,})", r.access_included),
            ("   Access add-on licences", r.access_addon),
            ("   Unused Access allowance", r.access_unused),
            ("Not counted", r.not_counted),
            (f"Growth allowance ceiling (+{s['growth_allowance_pct']:g}%)", r.growth_ceiling),
            (f"Premises device registration ({s['premises_reg_pct']:g}%)", r.premises_reg),
        ]

    def cost_rows(self, r, s):
        term = max(1, s["term_months"])
        return [
            (f"{SKUS['kw']} monthly", r.cost_kw),
            (f"{SKUS['ca']} monthly", r.cost_ca),
            (f"{SKUS['access']} monthly", r.cost_access),
            ("Total per month", r.monthly),
            ("Total per year", r.monthly * 12),
            (f"Total for {term}-month term", r.term_total),
        ]

    def quantities(self, extra=None):
        q = {lic: to_number(v.get()) for lic, v in self.qty_vars.items()}
        if extra:
            # Put what-if extras on the first licence type counted in that bucket.
            treatment = self.current_settings()["treatment"]
            for bucket, add in (("KW", extra["kw"]), ("CA", extra["ca"]), ("ACCESS", extra["access"])):
                lic = next((l for l in LICENSE_TYPES if treatment[l] == bucket), None)
                if lic and add:
                    q[lic] += add
        return q

    def recalc(self):
        quantities = self.quantities()
        s = self.current_settings()
        r = self.result = calculate(quantities, s)

        self.ea_label.config(text=f"{r.ea_licenses:,}")
        addons = []
        if r.ca_addon:
            addons.append(f"{r.ca_addon:,} Common Area add-on")
        if r.access_addon:
            addons.append(f"{r.access_addon:,} Access add-on")
        self.addon_label.config(text=("+ " + "  + ".join(addons) + " licences") if addons else "")

        notes = []
        if r.min_applied:
            notes.append(f"Raised to the EA minimum of {s['min_ea']:,} KWs "
                         f"({r.spare_kw:,} spare also cover devices above the allowance).")
        elif r.spare_kw:
            notes.append(f"Includes {r.spare_kw:,} extra KW licences for devices above the included allowance.")
        notes.append(f"You can grow to {r.growth_ceiling:,} KWs inside the "
                     f"{s['growth_allowance_pct']:g}% growth allowance.")
        self.note_label.config(text=" ".join(notes))

        self.tree.delete(*self.tree.get_children())
        for label, value in self.result_rows(r, s):
            self.tree.insert("", "end", text=label, values=(f"{value:,}",))
        cost = self.tree.insert("", "end", text=f"Cost (USD, {s['discount_pct']:g}% discount)", open=True)
        for label, value in self.cost_rows(r, s):
            self.tree.insert(cost, "end", text=label, values=(money(value),))
        if s["headroom_pct"]:
            parent = self.tree.insert("", "end", text=f"Quantities after {s['headroom_pct']:g}% head-room",
                                      open=True)
            for lic, qty, sized, _ in r.lines:
                self.tree.insert(parent, "end", text=f"{lic} ({qty:,})", values=(f"{sized:,}",))
        self.fill_scenarios(quantities, s)
        self.fill_whatif(s)

    def fill_scenarios(self, quantities, s):
        self.scen.delete(*self.scen.get_children())
        options = kw_scenarios(quantities, s)
        base = options[0][1].term_total
        for label, o in options:
            self.scen.insert("", "end", text=label, values=(
                f"{o.ea_licenses:,}", f"{o.ca_addon:,}", f"{o.access_addon:,}", money(o.monthly),
                money(o.term_total), ("+" if o.term_total > base else "") + money(o.term_total - base)))
        m = marginal_costs(s)
        self.scen_note.config(text=(
            f"Each extra EA KW costs {money(m['kw'])}/month but only frees {s['ca_pct']:g}% of a Common Area "
            f"and {s['access_pct']:g}% of an Access add-on, so it adds a net {money(m['kw_net'])}/month. "
            + ("Buying KWs only for your real knowledge workers (or the EA minimum) and add-ons for the rest "
               "is the cheapest option." if m["kw_net"] > 0 else
               "At these prices extra KWs are cheaper than add-ons while add-ons are needed.")))

    def fill_whatif(self, s):
        extra = {key: to_number(v.get()) for key, v in self.whatif_vars.items()}
        now = self.result
        after = calculate(self.quantities(extra), s)
        term = max(1, s["term_months"])
        rows = [
            ("EA KW licences", now.ea_licenses, after.ea_licenses, False),
            ("Common Area add-ons", now.ca_addon, after.ca_addon, False),
            ("Access add-ons", now.access_addon, after.access_addon, False),
            ("Monthly cost", now.monthly, after.monthly, True),
            ("Yearly cost", now.monthly * 12, after.monthly * 12, True),
            (f"{term}-month term cost", now.term_total, after.term_total, True),
        ]
        self.whatif.delete(*self.whatif.get_children())
        for label, a, b, is_money in rows:
            fmt = money if is_money else (lambda v: f"{v:,}")
            d = b - a
            self.whatif.insert("", "end", text=label,
                               values=(fmt(a), fmt(b), ("+" if d > 0 else "") + fmt(d)))
        p_kw, p_ca, p_ac = unit_prices(s)
        self.unit_note.config(text=(
            f"Once the included allowance is used up, each extra Common Area device costs {money(p_ca)}/month "
            f"({money(p_ca * term)} over {term} months) and each extra Access device {money(p_ac)}/month "
            f"({money(p_ac * term)}). Devices still inside the free allowance cost nothing; each new knowledge "
            f"worker costs {money(p_kw)}/month and adds {s['ca_pct']:g}% / {s['access_pct']:g}% free allowance."))

    def clear(self):
        for v in self.qty_vars.values():
            v.set("0")
        self.first_entry.focus_set()

    def save_defaults(self):
        self.settings = self.current_settings()
        save_settings(self.settings)
        messagebox.showinfo(APP_NAME, "Rules saved - they will load next time.")

    def reset_rules(self):
        d = DEFAULT_SETTINGS
        for lic in LICENSE_TYPES:
            self.how_vars[lic].set(TREATMENTS[d["treatment"][lic]])
        for key, _, _ in RULE_FIELDS + PRICE_FIELDS:
            self.rule_vars[key].set(f"{d[key]:g}")
        self.excess_mode.set(d["excess_mode"])

    def copy_summary(self):
        self.clipboard_clear()
        self.clipboard_append(summary_text(self.result, self.current_settings()))
        messagebox.showinfo(APP_NAME, "Summary copied to clipboard.")

    def export_excel(self):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except ImportError:
            messagebox.showerror(APP_NAME, "openpyxl is not installed (py -m pip install openpyxl).")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile="Flex EA sizing.xlsx",
                                            filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        r, s = self.result, self.current_settings()
        wb = Workbook()
        ws = wb.active
        ws.title = "Flex EA sizing"
        head = Font(bold=True, color="FFFFFF")
        fill = PatternFill("solid", fgColor="4F81BD")
        ws.append(["Existing licence", "Current qty", "Sized qty", "Flex user type"])
        for c in ws[1]:
            c.font, c.fill = head, fill
        for lic, qty, sized, how in r.lines:
            ws.append([lic, qty, sized, TREATMENTS[how]])
        ws.append([])
        for key, label, _ in RULE_FIELDS + PRICE_FIELDS:
            ws.append([label, s[key]])
        ws.append(["Devices above included amount",
                   "Common Area / Access add-ons" if s["excess_mode"] == "addon" else "Extra EA KW licences"])
        ws.append([])
        for label, value in self.result_rows(r, s):
            ws.append([label.strip(), value])
            if label in ("Flex EA Knowledge Worker licences", "   Common Area add-on licences",
                         "   Access add-on licences"):
                ws.cell(ws.max_row, 1).font = ws.cell(ws.max_row, 2).font = Font(bold=True)
        ws.append([])
        for label, value in self.cost_rows(r, s):
            ws.append([label, round(value, 2)])
            ws.cell(ws.max_row, 2).number_format = "#,##0.00"
        ws.cell(ws.max_row, 1).font = ws.cell(ws.max_row, 2).font = Font(bold=True)

        sc = wb.create_sheet("More KW vs add-ons")
        sc.append(["Option", "EA KW", "CA add-on", "Access add-on", "Monthly", "Term total", "vs recommended"])
        for c in sc[1]:
            c.font, c.fill = head, fill
        options = kw_scenarios(self.quantities(), s)
        for label, o in options:
            sc.append([label, o.ea_licenses, o.ca_addon, o.access_addon, round(o.monthly, 2),
                       round(o.term_total, 2), round(o.term_total - options[0][1].term_total, 2)])
        for row in sc.iter_rows(min_row=2, min_col=5, max_col=7):
            for c in row:
                c.number_format = "#,##0.00"
        sc.column_dimensions["A"].width = 24
        for col in "BCDEFG":
            sc.column_dimensions[col].width = 16

        ws.append([])
        ws.append(["Source", DATA_SHEET_URL])
        ws.column_dimensions["A"].width = 42
        for col in "BCD":
            ws.column_dimensions[col].width = 20
        try:
            wb.save(path)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not save:\n{e}")
            return
        os.startfile(path)


C = {"bg": "#F3F6FA", "surface": "#FFFFFF", "ink": "#1E2A38", "muted": "#6B7785", "line": "#D6DDE6",
     "accent": "#049FD9", "accent_dark": "#0379A6", "accent_soft": "#DDF2FB", "navy": "#0B2A4A"}
F = "Segoe UI"


def setup_style(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    root.configure(background=C["bg"])
    s.configure(".", font=(F, 10), background=C["bg"], foreground=C["ink"])
    s.configure("TLabelframe", background=C["surface"], bordercolor=C["line"], relief="solid", padding=12)
    s.configure("TLabelframe.Label", background=C["bg"], foreground=C["navy"], font=(F, 11, "bold"))
    for w in ("TFrame", "TLabel", "TRadiobutton"):
        s.configure(w, background=C["surface"])
    s.configure("App.TFrame", background=C["bg"])
    s.configure("TNotebook", background=C["surface"], borderwidth=0)
    s.configure("TNotebook.Tab", font=(F, 10, "bold"), padding=(14, 6), background=C["bg"], foreground=C["muted"])
    s.map("TNotebook.Tab", background=[("selected", C["accent_soft"])], foreground=[("selected", C["navy"])])
    s.configure("TButton", padding=(12, 6), background=C["surface"], bordercolor=C["line"])
    s.map("TButton", background=[("active", C["accent_soft"])])
    s.configure("Treeview", rowheight=26, fieldbackground=C["surface"], background=C["surface"], bordercolor=C["line"])
    s.configure("Treeview.Heading", font=(F, 9, "bold"), background=C["bg"], foreground=C["muted"], relief="flat")
    s.map("Treeview", background=[("selected", C["accent_soft"])], foreground=[("selected", C["ink"])])
    for w in ("TEntry", "TSpinbox", "TCombobox"):
        s.configure(w, fieldbackground=C["surface"], bordercolor=C["line"], padding=4)
    s.map("TCombobox", fieldbackground=[("readonly", C["surface"])],
          selectbackground=[("readonly", C["surface"])], selectforeground=[("readonly", C["ink"])])
    root.option_add("*TCombobox*Listbox.font", (F, 10))


def header(root):
    bar = tk.Frame(root, background=C["navy"], padx=18, pady=12)
    bar.pack(fill="x")
    tk.Frame(root, background=C["accent"], height=3).pack(fill="x")
    tk.Label(bar, text=APP_NAME, bg=C["navy"], fg="#FFFFFF", font=(F, 17, "bold")).pack(anchor="w")
    tk.Label(bar, text="Collaboration Flex Plan 3.0  ·  Enterprise Agreement  ·  On-premises calling",
             bg=C["navy"], fg="#9FC3E0", font=(F, 10)).pack(anchor="w")


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    root.title(APP_NAME)
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    if os.path.exists(icon):
        root.iconbitmap(icon)
    setup_style(root)
    header(root)
    App(root).pack(fill="both", expand=True)
    root.minsize(1060, 720)
    root.mainloop()


if __name__ == "__main__":
    main()
