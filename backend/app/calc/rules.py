"""Calculation rules grounded in the loaded ISO corpus.

Only formulas whose defining passages exist in the active verified corpus are
implemented (see ``evidence.REQ``). Each rule lists the evidence it needs;
if any passage is missing (document deleted, superseded, not indexed) the
engine refuses with the exact phrase "Bu kaynak setinde doğrulayamadım".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.calc.iso286_table import TableParseError, lookup, normalize_grade, parse_table1
from app.calc.types import Diagnostic, EvidenceMatch, InputSpec, OutputValue
from app.calc.units import fmt_num


class RuleRefusal(Exception):
    """The rule cannot produce a verified result (reason is diagnostic)."""


class RuleInputError(Exception):
    pass


@dataclass
class RuleContext:
    values: dict[str, float]  # canonical units
    provided: set[str]
    evidence: dict[str, EvidenceMatch]
    assumptions: list[str] = field(default_factory=list)
    constants: list[dict[str, Any]] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


def _out(key: str, label: str, value: float, unit: str, formula_id: str, expression: str,
         decimals: int = 4, unrounded: float | None = None, abs_tol: float | None = None) -> OutputValue:
    return OutputValue(key=key, label=label, value=value, unit=unit, formula_id=formula_id,
                       expression=expression, display=f"{fmt_num(value, decimals)} {unit}".strip(),
                       unrounded=unrounded, abs_tol=abs_tol)


class Rule:
    calc_type: str = ""
    title: str = ""
    description: str = ""
    inputs: tuple[InputSpec, ...] = ()

    def evidence_ids(self, provided: set[str], values: dict[str, float]) -> list[str]:
        raise NotImplementedError

    def apply_defaults(self, ctx: RuleContext) -> None:
        pass

    def compute(self, ctx: RuleContext) -> list[OutputValue]:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {
            "calc_type": self.calc_type,
            "title": self.title,
            "description": self.description,
            "inputs": [
                {"key": s.key, "label": s.label, "kind": s.kind, "required": s.required,
                 "min": s.min_value, "max": s.max_value, "description": s.description}
                for s in self.inputs
            ],
            "evidence": self.evidence_ids(set(), {}),
        }


# ---------------------------------------------------------------------------
# ISO 53 basic rack defaults (used only when the user does not provide them)
# ---------------------------------------------------------------------------
BASIC_RACK_EVIDENCE = "iso53.table2"


def _basic_rack_defaults(ctx: RuleContext, keys: tuple[str, ...]) -> None:
    ev = ctx.evidence.get(BASIC_RACK_EVIDENCE)
    table = {"alpha_n": (20.0, "°", "α_P = 20°"), "h_aP_star": (1.0, "", "h_aP = 1·m"),
             "h_fP_star": (1.25, "", "h_fP = 1,25·m")}
    for k in keys:
        if k in ctx.provided:
            continue
        val, unit, label = table[k]
        ctx.values[k] = val
        ctx.constants.append({
            "key": k, "value": val, "unit": unit or "1", "label": label,
            "source": ev.public() if ev else None,
            "note": "ISO 53:1998 Tablo 2 standart temel kremayer (standard basic rack) oranı",
        })


def _needs_basic_rack(provided: set[str], keys: tuple[str, ...]) -> bool:
    return any(k not in provided for k in keys)


# ---------------------------------------------------------------------------
class CylindricalGearGeometry(Rule):
    calc_type = "cylindrical_gear_geometry"
    title = "Silindirik evolvent dişli geometrisi (ISO 21771 / ISO 53)"
    description = (
        "Dış dişli (external gear) için referans çapı, temel daire çapı, adımlar, diş başı/dibi yükseklikleri "
        "ve diş başı/dibi çapları. Formüller ISO 21771:2007'den; varsayılan temel kremayer oranları ISO 53:1998 Tablo 2'den."
    )
    inputs = (
        InputSpec("z", "Diş sayısı z (number of teeth)", "integer", True, 1, 100000),
        InputSpec("m_n", "Normal modül m_n (normal module)", "length", True, 1e-6, 1e5),
        InputSpec("alpha_n", "Normal kavrama açısı α_n (normal pressure angle)", "angle", False, 0.1, 89.0,
                  "Verilmezse ISO 53 Tablo 2: α_P = 20°"),
        InputSpec("beta", "Helis açısı β (helix angle)", "angle", False, 0.0, 89.0, "Verilmezse düz dişli: β = 0°"),
        InputSpec("x", "Profil kaydırma katsayısı x (profile shift coefficient)", "dimensionless", False, -10, 10,
                  "Verilmezse x = 0 (varsayım)"),
        InputSpec("k", "Diş başı değiştirme katsayısı k (tip alteration coefficient)", "dimensionless", False, -10, 10,
                  "Verilmezse k = 0 (varsayım)"),
        InputSpec("h_aP_star", "Temel kremayer diş başı katsayısı h_aP*", "dimensionless", False, 0.01, 10,
                  "Verilmezse ISO 53 Tablo 2: 1"),
        InputSpec("h_fP_star", "Temel kremayer diş dibi katsayısı h_fP*", "dimensionless", False, 0.01, 10,
                  "Verilmezse ISO 53 Tablo 2: 1,25"),
    )
    _rack_keys = ("alpha_n", "h_aP_star", "h_fP_star")

    def evidence_ids(self, provided, values):
        ids = ["iso21771.eq1", "iso21771.eq2", "iso21771.eq13", "iso21771.eq14", "iso21771.eq19",
               "iso21771.eq23", "iso21771.eq24", "iso21771.eq28", "iso21771.eq33", "iso21771.eq34",
               "iso21771.eq35", "iso21771.eq36_37"]
        if _needs_basic_rack(provided, self._rack_keys):
            ids.append(BASIC_RACK_EVIDENCE)
        return ids

    def apply_defaults(self, ctx):
        _basic_rack_defaults(ctx, self._rack_keys)
        if "beta" not in ctx.provided:
            ctx.values["beta"] = 0.0
            ctx.assumptions.append("Helis açısı verilmedi: düz dişli (spur gear) kabul edildi, β = 0°.")
        if "x" not in ctx.provided:
            ctx.values["x"] = 0.0
            ctx.assumptions.append("Profil kaydırma verilmedi: x = 0 kabul edildi.")
        if "k" not in ctx.provided:
            ctx.values["k"] = 0.0
            ctx.assumptions.append("Diş başı değiştirme verilmedi: k = 0 kabul edildi.")
        ctx.assumptions.append("Yalnızca dış dişli (external gear) için hesaplandı (z > 0).")

    def compute(self, ctx):
        v = ctx.values
        z, mn, an, beta, x, k = int(v["z"]), v["m_n"], v["alpha_n"], v["beta"], v["x"], v["k"]
        haP, hfP = v["h_aP_star"] * mn, v["h_fP_star"] * mn
        b, a = math.radians(beta), math.radians(an)
        mt = mn / math.cos(b)
        at = math.atan(math.tan(a) / math.cos(b))
        d = z * mt
        db = d * math.cos(at)
        pn = math.pi * mn
        pt = math.pi * mt
        pbt = pt * math.cos(at)
        ha = haP + x * mn + k * mn
        hf = hfP - x * mn
        h = haP + k * mn + hfP
        da = d + 2 * (x * mn + haP + k * mn)
        df = d - 2 * (hfP - x * mn)
        if df <= 0:
            raise RuleInputError("Diş dibi çapı sıfır veya negatif çıktı; girişleri kontrol edin.")
        at_deg = math.degrees(at)
        ctx.trace += [
            f"h_aP = {fmt_num(v['h_aP_star'])}·m_n = {fmt_num(haP)} mm; h_fP = {fmt_num(v['h_fP_star'])}·m_n = {fmt_num(hfP)} mm",
            f"m_t = m_n / cos β = {fmt_num(mn)} / cos {fmt_num(beta)}° = {fmt_num(mt)} mm",
            f"α_t = arctan(tan α_n / cos β) = arctan(tan {fmt_num(an)}° / cos {fmt_num(beta)}°) = {fmt_num(at_deg)}°",
            f"d = z·m_t = {z}·{fmt_num(mt)} = {fmt_num(d)} mm",
            f"d_b = d·cos α_t = {fmt_num(d)}·cos {fmt_num(at_deg)}° = {fmt_num(db)} mm",
            f"p_n = π·m_n = {fmt_num(pn)} mm; p_t = π·m_t = {fmt_num(pt)} mm; p_bt = p_t·cos α_t = {fmt_num(pbt)} mm",
            f"h_a = h_aP + x·m_n + k·m_n = {fmt_num(ha)} mm; h_f = h_fP − x·m_n = {fmt_num(hf)} mm; h = {fmt_num(h)} mm",
            f"d_a = d + 2(x·m_n + h_aP + k·m_n) = {fmt_num(da)} mm; d_f = d − 2(h_fP − x·m_n) = {fmt_num(df)} mm",
        ]
        return [
            _out("m_t", "Alın modülü m_t (transverse module)", mt, "mm", "ISO21771:2007 Eş.(2)", "m_t = m_n / cos β"),
            _out("alpha_t", "Alın kavrama açısı α_t (transverse pressure angle)", at_deg, "°", "ISO21771:2007 Eş.(14)",
                 "tan α_n = tan α_t · cos β"),
            _out("d", "Referans çapı d (reference diameter)", d, "mm", "ISO21771:2007 Eş.(1)", "d = z·m_n / cos β"),
            _out("d_b", "Temel daire çapı d_b (base diameter)", db, "mm", "ISO21771:2007 Eş.(13)/(19)", "d_b = d·cos α_t"),
            _out("p_n", "Normal adım p_n (normal pitch)", pn, "mm", "ISO21771:2007 Eş.(24)", "p_n = π·m_n"),
            _out("p_t", "Alın adımı p_t (transverse pitch)", pt, "mm", "ISO21771:2007 Eş.(23)", "p_t = π·m_t"),
            _out("p_bt", "Alın temel adımı p_bt (transverse base pitch)", pbt, "mm", "ISO21771:2007 Eş.(28)",
                 "p_bt = p_t·cos α_t"),
            _out("h_a", "Diş başı yüksekliği h_a (addendum)", ha, "mm", "ISO21771:2007 Eş.(36)", "h_a = h_aP + x·m_n + k·m_n"),
            _out("h_f", "Diş dibi yüksekliği h_f (dedendum)", hf, "mm", "ISO21771:2007 Eş.(37)", "h_f = h_fP − x·m_n"),
            _out("h", "Diş yüksekliği h (tooth depth)", h, "mm", "ISO21771:2007 Eş.(35)", "h = h_aP + k·m_n + h_fP"),
            _out("d_a", "Diş başı çapı d_a (tip diameter)", da, "mm", "ISO21771:2007 Eş.(33)",
                 "d_a = d + 2(x·m_n + h_aP + k·m_n)"),
            _out("d_f", "Diş dibi çapı d_f (root diameter)", df, "mm", "ISO21771:2007 Eş.(34)", "d_f = d − 2(h_fP − x·m_n)"),
        ]


# ---------------------------------------------------------------------------
class TransverseModule(Rule):
    """m_t = m_n / cos β on its own: the number of teeth is not part of the relation."""

    calc_type = "transverse_module"
    title = "Alın modülü m_t (ISO 21771:2007 Eş. 2)"
    description = (
        "Helisel dişli için alın modülü (transverse module) m_t = m_n / cos β. Yalnızca normal modül m_n ve "
        "helis açısı β (derece) gerekir; diş sayısı gerekmez."
    )
    inputs = (
        InputSpec("m_n", "Normal modül m_n (normal module)", "length", True, 1e-6, 1e5),
        InputSpec("beta", "Helis açısı β (helix angle), derece", "angle", True, 0.0, 89.0),
    )

    def evidence_ids(self, provided, values):
        return ["iso21771.eq2"]

    def compute(self, ctx):
        mn, beta = ctx.values["m_n"], ctx.values["beta"]  # beta is canonical degrees (units.to_canonical)
        mt = mn / math.cos(math.radians(beta))
        ctx.assumptions.append("β derece (°) olarak alındı; cos β için radyana çevrildi.")
        ctx.trace.append(f"m_t = m_n / cos β = {fmt_num(mn)} / cos {fmt_num(beta)}° = {fmt_num(mt)} mm")
        return [_out("m_t", "Alın modülü m_t (transverse module)", mt, "mm", "ISO21771:2007 Eş.(2)", "m_t = m_n / cos β")]


# ---------------------------------------------------------------------------
class GearPair(Rule):
    calc_type = "gear_pair"
    title = "Dişli çifti: oran ve çalışma kavrama açısı (ISO 21771)"
    description = (
        "Dış dişli çifti için dişli oranı u = z2/z1 ve verilen çalışma eksen mesafesi a_w için çalışma alın kavrama açısı α_wt "
        "(ISO 21771:2007 Eş. 52 ve 54)."
    )
    inputs = (
        InputSpec("z1", "Pinyon diş sayısı z1", "integer", True, 1, 100000),
        InputSpec("z2", "Çark diş sayısı z2", "integer", True, 1, 100000),
        InputSpec("m_n", "Normal modül m_n", "length", True, 1e-6, 1e5),
        InputSpec("alpha_n", "Normal kavrama açısı α_n", "angle", False, 0.1, 89.0, "Verilmezse ISO 53 Tablo 2: 20°"),
        InputSpec("beta", "Helis açısı β", "angle", False, 0.0, 89.0, "Verilmezse β = 0°"),
        InputSpec("a_w", "Çalışma eksen mesafesi a_w (centre distance)", "length", False, 1e-6, 1e7,
                  "Verilirse α_wt hesaplanır"),
    )

    def evidence_ids(self, provided, values):
        ids = ["iso21771.eq52", "iso21771.eq1", "iso21771.eq2", "iso21771.eq14"]
        if "a_w" in provided:
            ids.append("iso21771.eq54")
        if "alpha_n" not in provided:
            ids.append(BASIC_RACK_EVIDENCE)
        return ids

    def apply_defaults(self, ctx):
        _basic_rack_defaults(ctx, ("alpha_n",))
        if "beta" not in ctx.provided:
            ctx.values["beta"] = 0.0
            ctx.assumptions.append("Helis açısı verilmedi: β = 0° (düz dişli) kabul edildi.")
        ctx.assumptions.append("Dış dişli çifti (external gear pair) kabul edildi.")

    def compute(self, ctx):
        v = ctx.values
        z1, z2 = int(v["z1"]), int(v["z2"])
        if z1 > z2:
            ctx.diagnostics.append(Diagnostic("info", "pinion_swap",
                                              "ISO 21771'e göre 1 indisi küçük dişliye (pinyon) verilir; z1 ve z2 yer değiştirildi."))
            z1, z2 = z2, z1
        mn, an, beta = v["m_n"], v["alpha_n"], v["beta"]
        b = math.radians(beta)
        mt = mn / math.cos(b)
        at = math.atan(math.tan(math.radians(an)) / math.cos(b))
        u = z2 / z1
        d1, d2 = z1 * mt, z2 * mt
        outs = [
            _out("u", "Dişli oranı u (gear ratio)", u, "", "ISO21771:2007 Eş.(52)", "u = z2 / z1"),
            _out("d1", "Pinyon referans çapı d1", d1, "mm", "ISO21771:2007 Eş.(1)", "d = z·m_n / cos β"),
            _out("d2", "Çark referans çapı d2", d2, "mm", "ISO21771:2007 Eş.(1)", "d = z·m_n / cos β"),
            _out("alpha_t", "Alın kavrama açısı α_t", math.degrees(at), "°", "ISO21771:2007 Eş.(14)",
                 "tan α_n = tan α_t · cos β"),
        ]
        ctx.trace += [
            f"u = z2/z1 = {z2}/{z1} = {fmt_num(u)}",
            f"d1 = {z1}·{fmt_num(mt)} = {fmt_num(d1)} mm; d2 = {z2}·{fmt_num(mt)} = {fmt_num(d2)} mm",
        ]
        if "a_w" in ctx.provided:
            aw = v["a_w"]
            arg = (mn * (z1 + z2) / (2 * aw * math.cos(b))) * math.cos(at)
            if not -1.0 <= arg <= 1.0:
                raise RuleInputError("Verilen eksen mesafesi için α_wt tanımsız (arccos argümanı |x| > 1).")
            awt = math.degrees(math.acos(arg))
            outs.append(_out("alpha_wt", "Çalışma alın kavrama açısı α_wt (working transverse pressure angle)", awt, "°",
                             "ISO21771:2007 Eş.(54)", "α_wt = arccos[ m_n(z1+z2)/(2·a_w·cos β) · cos α_t ]"))
            ctx.trace.append(
                f"α_wt = arccos[{fmt_num(mn)}·({z1}+{z2})/(2·{fmt_num(aw)}·cos {fmt_num(beta)}°)·cos {fmt_num(math.degrees(at))}°] = {fmt_num(awt)}°"
            )
        return outs


# ---------------------------------------------------------------------------
def iso1328_round(value_um: float) -> float:
    """ISO 1328-1:2013 5.2.3 rounding rules (half-up)."""
    d = Decimal(repr(value_um))
    if value_um > 10:
        return float(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if value_um >= 5.0:
        return float((d * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2)
    return float(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


class Iso1328FlankTolerance(Rule):
    calc_type = "iso1328_flank_tolerance"
    title = "Diş yanağı toleransları (ISO 1328-1:2013)"
    description = (
        "Tolerans sınıfı A (1–11) için tek adım, toplam kümülatif adım, profil ve helis toleransları "
        "(ISO 1328-1:2013 Formül 5–12, 5.2.3 yuvarlama)."
    )
    inputs = (
        InputSpec("m_n", "Normal modül m_n", "length", True, 1e-6, 1e5),
        InputSpec("d", "Referans çapı d (verilmezse z, m_n, β'dan)", "length", False, 1e-6, 1e7),
        InputSpec("z", "Diş sayısı z (d yoksa zorunlu)", "integer", False, 1, 100000),
        InputSpec("beta", "Helis açısı β", "angle", False, 0.0, 89.0),
        InputSpec("b", "Diş genişliği b (facewidth) - helis toleransları için", "length", False, 1e-6, 1e6),
        InputSpec("A", "Tolerans sınıfı A (flank tolerance class)", "grade", True, 1, 11),
    )

    def evidence_ids(self, provided, values):
        ids = ["iso1328.scope", "iso1328.no_extrapolation", "iso1328.step", "iso1328.rounding",
               "iso1328.f5", "iso1328.f6", "iso1328.f7", "iso1328.f8", "iso1328.f9"]
        if "b" in provided:
            ids += ["iso1328.f10", "iso1328.f11", "iso1328.f12"]
        if "d" not in provided:
            ids += ["iso21771.eq1", "iso21771.eq2"]
        return ids

    def apply_defaults(self, ctx):
        v = ctx.values
        if "d" not in ctx.provided:
            if "z" not in ctx.provided:
                raise RuleInputError("Referans çapı d veya diş sayısı z verilmelidir.")
            beta = v.get("beta", 0.0)
            if "beta" not in ctx.provided:
                ctx.assumptions.append("Helis açısı verilmedi: β = 0° kabul edildi (d hesabı için).")
            v["d"] = int(v["z"]) * v["m_n"] / math.cos(math.radians(beta))
            ctx.trace.append(f"d = z·m_n / cos β = {fmt_num(v['d'])} mm (ISO 21771 Eş. 1, türetilmiş)")
        if "b" not in ctx.provided:
            ctx.assumptions.append("Diş genişliği b verilmedi: helis toleransları hesaplanmadı.")

    def compute(self, ctx):
        v = ctx.values
        A = v["A"]
        if A != int(A) or not 1 <= A <= 11:
            raise RuleInputError("Tolerans sınıfı A, 1 ile 11 arasında tam sayı olmalıdır.")
        A = int(A)
        mn, d = v["m_n"], v["d"]
        # 5.2.1: no extrapolation beyond the scope ranges
        checks = [("m_n", mn, 0.5, 70.0, "0,5 mm ≤ m_n ≤ 70 mm"), ("d", d, 5.0, 15000.0, "5 mm ≤ d ≤ 15 000 mm")]
        if "z" in ctx.provided:
            checks.append(("z", v["z"], 5, 1000, "5 ≤ z ≤ 1 000"))
        if "b" in ctx.provided:
            checks.append(("b", v["b"], 4.0, 1200.0, "4 mm ≤ b ≤ 1 200 mm"))
        if "beta" in ctx.provided:
            checks.append(("beta", v["beta"], 0.0, 45.0, "β ≤ 45°"))
        for key, val, lo, hi, text in checks:
            if not lo <= val <= hi:
                raise RuleRefusal(
                    f"{key} = {fmt_num(val)} ISO 1328-1:2013 uygulama aralığı dışında ({text}); formüller ekstrapole edilemez."
                )
        f = math.sqrt(2) ** (A - 5)
        fpT = (0.001 * d + 0.4 * mn + 5) * f
        FpT = (0.002 * d + 0.55 * math.sqrt(d) + 0.7 * mn + 12) * f
        fHaT = (0.4 * mn + 0.001 * d + 4) * f
        ffaT = (0.55 * mn + 5) * f
        FaT = math.sqrt(fHaT ** 2 + ffaT ** 2)
        ctx.trace.append(f"Sınıf çarpanı (√2)^(A−5) = (√2)^({A}−5) = {fmt_num(f, 6)}")
        items = [
            ("f_pT", "Tek adım toleransı f_pT (single pitch tolerance)", fpT, "ISO1328-1:2013 F.(5)",
             "f_pT = (0,001·d + 0,4·m_n + 5)·(√2)^(A−5)"),
            ("F_pT", "Toplam kümülatif adım toleransı F_pT", FpT, "ISO1328-1:2013 F.(6)",
             "F_pT = (0,002·d + 0,55·√d + 0,7·m_n + 12)·(√2)^(A−5)"),
            ("f_HaT", "Profil eğim toleransı f_HαT (±)", fHaT, "ISO1328-1:2013 F.(7)",
             "f_HαT = (0,4·m_n + 0,001·d + 4)·(√2)^(A−5)"),
            ("f_faT", "Profil form toleransı f_fαT", ffaT, "ISO1328-1:2013 F.(8)", "f_fαT = (0,55·m_n + 5)·(√2)^(A−5)"),
            ("F_aT", "Toplam profil toleransı F_αT", FaT, "ISO1328-1:2013 F.(9)", "F_αT = √(f_HαT² + f_fαT²) (yuvarlanmamış)"),
        ]
        if "b" in ctx.provided:
            bw = v["b"]
            fHbT = (0.05 * math.sqrt(d) + 0.35 * math.sqrt(bw) + 4) * f
            ffbT = (0.07 * math.sqrt(d) + 0.45 * math.sqrt(bw) + 4) * f
            FbT = math.sqrt(fHbT ** 2 + ffbT ** 2)
            items += [
                ("f_HbT", "Helis eğim toleransı f_HβT (±)", fHbT, "ISO1328-1:2013 F.(10)",
                 "f_HβT = (0,05·√d + 0,35·√b + 4)·(√2)^(A−5)"),
                ("f_fbT", "Helis form toleransı f_fβT", ffbT, "ISO1328-1:2013 F.(11)",
                 "f_fβT = (0,07·√d + 0,45·√b + 4)·(√2)^(A−5)"),
                ("F_bT", "Toplam helis toleransı F_βT", FbT, "ISO1328-1:2013 F.(12)", "F_βT = √(f_HβT² + f_fβT²) (yuvarlanmamış)"),
            ]
        outs = []
        for key, label, raw, fid, expr in items:
            r = iso1328_round(raw)
            outs.append(_out(key, label, r, "µm", fid, expr, decimals=1, unrounded=raw))
            ctx.trace.append(f"{key}: hesap {fmt_num(raw, 4)} µm → 5.2.3 yuvarlama {fmt_num(r, 1)} µm")
        return outs


# ---------------------------------------------------------------------------
class Iso286StandardTolerance(Rule):
    calc_type = "iso286_it_tolerance"
    title = "Standart tolerans değeri IT (ISO 286-1:2010 Tablo 1)"
    description = "Anma ölçüsü ve standart tolerans derecesi (IT01–IT18) için ISO 286-1 Tablo 1 değeri; tablo etkin kaynaktan okunur."
    inputs = (
        InputSpec("nominal_size", "Anma ölçüsü D (nominal size)", "length", True, 1e-9, 3150.0),
        InputSpec("grade", "Standart tolerans derecesi (ör. 7 → IT7; 01 → IT01; 0 → IT0)", "grade", True, -1, 18),
    )

    def evidence_ids(self, provided, values):
        return ["iso286.table1"]

    def compute(self, ctx):
        D = ctx.values["nominal_size"]
        g_raw = ctx.values["grade"]
        grade = "IT01" if g_raw == -1 else f"IT{int(g_raw)}"
        grade = normalize_grade(grade)
        ev = ctx.evidence["iso286.table1"]
        try:
            rows = parse_table1(ev.page_text)
        except TableParseError as exc:
            raise RuleRefusal(f"ISO 286-1 Tablo 1 kaynaktan güvenilir biçimde okunamadı: {exc}") from exc
        found = lookup(rows, D, grade)
        if found is None:
            raise RuleRefusal(f"{grade} için {fmt_num(D)} mm anma ölçüsünde ISO 286-1 Tablo 1'de değer yok.")
        row, val_um = found
        rng = f"{'—' if row.above == 0 else fmt_num(row.above)} < D ≤ {fmt_num(row.up_to)} mm"
        ctx.trace += [
            f"Tablo 1 kaynak sayfasından {len(rows)} satır okundu ve yapısal olarak doğrulandı.",
            f"D = {fmt_num(D)} mm → satır: {rng}",
            f"{grade} = {fmt_num(val_um, 3)} µm",
        ]
        return [
            _out("IT", f"Standart tolerans {grade}", val_um, "µm", "ISO286-1:2010 Tablo 1",
                 f"{grade} ({rng})", decimals=3),
            _out("IT_mm", f"Standart tolerans {grade} (mm)", val_um / 1000.0, "mm", "ISO286-1:2010 Tablo 1",
                 f"{grade} ({rng})", decimals=5),
        ]


class _Iso286BasicClass(Rule):
    """Limit deviations of the basic hole H / basic shaft h (ISO 286-1:2010).

    Only H and h are implemented: their fundamental deviation is zero by definition,
    so no fundamental-deviation table (Tables 2-5) is needed. Other letters are not
    supported and fall back to the verified-text path or are refused.
    """
    letter = "H"
    inputs = (
        InputSpec("nominal_size", "Anma ölçüsü D (nominal size)", "length", True, 1e-9, 3150.0),
        InputSpec("grade", "Standart tolerans derecesi (ör. 7 → IT7)", "grade", True, 1, 18),
    )

    def evidence_ids(self, provided, values):
        if self.letter == "H":
            return ["iso286.table1", "iso286.basic_hole", "iso286.hole_H"]
        return ["iso286.table1", "iso286.basic_shaft", "iso286.shaft_h", "iso286.shaft_ei"]

    def compute(self, ctx):
        D = ctx.values["nominal_size"]
        grade = f"IT{int(ctx.values['grade'])}"
        try:
            rows = parse_table1(ctx.evidence["iso286.table1"].page_text)
        except TableParseError as exc:
            raise RuleRefusal(f"ISO 286-1 Tablo 1 kaynaktan güvenilir biçimde okunamadı: {exc}") from exc
        found = lookup(rows, D, grade)
        if found is None:
            raise RuleRefusal(f"{grade} için {fmt_num(D)} mm anma ölçüsünde ISO 286-1 Tablo 1'de değer yok.")
        row, it_um = found
        cls = f"{self.letter}{int(ctx.values['grade'])}"
        rng = f"{'—' if row.above == 0 else fmt_num(row.above)} < D ≤ {fmt_num(row.up_to)} mm"
        if self.letter == "H":
            lower_dev, upper_dev = 0.0, it_um
            devs = [
                _out("EI", f"{cls} alt sınır sapması EI (lower limit deviation)", 0.0, "µm", "ISO286-1:2010 3.1.4 / Ek B",
                     "EI = 0 (temel delik H)", decimals=3),
                _out("ES", f"{cls} üst sınır sapması ES (upper limit deviation)", upper_dev, "µm", "ISO286-1:2010 Ek B",
                     f"ES = EI + IT = 0 + {grade}", decimals=3),
            ]
        else:
            lower_dev, upper_dev = -it_um, 0.0
            devs = [
                _out("es", f"{cls} üst sınır sapması es (upper limit deviation)", 0.0, "µm", "ISO286-1:2010 3.1.6 / Şekil 9",
                     "es = 0 (temel mil h)", decimals=3),
                _out("ei", f"{cls} alt sınır sapması ei (lower limit deviation)", lower_dev, "µm", "ISO286-1:2010 Şekil 9",
                     f"ei = es − IT = 0 − {grade}", decimals=3),
            ]
        lower_size, upper_size = D + lower_dev / 1000.0, D + upper_dev / 1000.0
        ctx.trace += [
            f"Tablo 1 kaynak sayfasından {len(rows)} satır okundu; D = {fmt_num(D)} mm → satır {rng}",
            f"{grade} = {fmt_num(it_um, 3)} µm",
            (f"Temel delik H: EI = 0; ES = EI + IT = {fmt_num(upper_dev, 3)} µm" if self.letter == "H"
             else f"Temel mil h: es = 0; ei = es − IT = {fmt_num(lower_dev, 3)} µm"),
            f"Sınır ölçüler: {fmt_num(lower_size, 4)} mm … {fmt_num(upper_size, 4)} mm",
        ]
        return [_out("IT", f"Standart tolerans {grade}", it_um, "µm", "ISO286-1:2010 Tablo 1", f"{grade} ({rng})",
                     decimals=3)] + devs + [
            _out("lower_size", f"{fmt_num(D)} {cls} alt sınır ölçüsü", lower_size, "mm", "ISO286-1:2010 3.2.8",
                 "D + alt sınır sapması", decimals=4, abs_tol=0.0005),
            _out("upper_size", f"{fmt_num(D)} {cls} üst sınır ölçüsü", upper_size, "mm", "ISO286-1:2010 3.2.8",
                 "D + üst sınır sapması", decimals=4, abs_tol=0.0005),
        ]


class Iso286HoleH(_Iso286BasicClass):
    calc_type = "iso286_hole_H"
    letter = "H"
    title = "Delik tolerans sınıfı H (ISO 286-1:2010, temel delik)"
    description = ("H toleranslı delik için IT değeri, sınır sapmaları (EI = 0, ES = +IT) ve sınır ölçüleri. "
                   "IT, ISO 286-1 Tablo 1'den etkin kaynaktan okunur.")


class Iso286ShaftH(_Iso286BasicClass):
    calc_type = "iso286_shaft_h"
    letter = "h"
    title = "Mil tolerans sınıfı h (ISO 286-1:2010, temel mil)"
    description = ("h toleranslı mil için IT değeri, sınır sapmaları (es = 0, ei = −IT) ve sınır ölçüleri. "
                   "IT, ISO 286-1 Tablo 1'den etkin kaynaktan okunur.")


RULES: dict[str, Rule] = {r.calc_type: r for r in (
    CylindricalGearGeometry(), TransverseModule(), GearPair(), Iso1328FlankTolerance(), Iso286StandardTolerance(),
    Iso286HoleH(), Iso286ShaftH(),
)}
