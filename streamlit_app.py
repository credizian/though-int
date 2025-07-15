import streamlit as st
import pandas as pd
import re
import matplotlib.pyplot as plt
from io import BytesIO

st.set_page_config(page_title="PA Response Generator", layout="wide")

st.title("Prior‑Authorization (PA) Response Generator")

st.markdown(
    """
Upload a **CSV** or **Excel** file that follows the same column structure as the
notional patient dataset. The app automatically answers the 13 PA‑form questions for
each patient, shows summary visuals, highlights anomalies, and lets you download the
results.
    """
)

# ──────────────────────────────────────────────────────────────
# Helper functions & rule sets
# ──────────────────────────────────────────────────────────────

def std(text: str) -> str:
    return str(text).lower()

KEYWORDS = {
    "Upper Extremity": ["shoulder", "elbow", "wrist", "hand", "arm", "rotator", "carpal"],
    "Lower Extremity": ["hip", "thigh", "knee", "ankle", "foot", "leg", "acl", "hamstring"],
    "Spine/Trunk": ["spine", "back", "lumbar", "thoracic", "cervical", "trunk"],
    "Head/Face/Jaw": ["head", "face", "jaw", "tmj", "concussion"],
}
PREFIX_BUCKETS = {
    "Upper Extremity": ("M75", "S4", "S43", "S45"),
    "Lower Extremity": ("M17", "S83", "S82", "S86"),
    "Spine/Trunk": ("M54", "S23", "S33", "M51", "M50"),
    "Head/Face/Jaw": ("S02", "S06"),
}
ICD_LATERALITY = {"1": "Right", "2": "Left", "3": "Bilateral"}
SURGERY_KEYWORDS = {
    "Joint Replacement Surgery": ["replacement", "arthroplasty", "thr", "tkr"],
    "Arthroscopic/Minimally Invasive Joint Surgery": ["arthroscopic", "arthroscopy", "scope"],
    "Spine Surgery": ["laminectomy", "fusion", "discectomy"],
    "Fracture/Trauma Repair": ["fracture", "orif", "hardware", "fixation"],
}
SPECIAL_TESTS = ["lachman", "hawkins", "phalen", "drawer", "apprehension"]


def any_kw(text: str, kws):
    t = std(text)
    return any(k in t for k in kws)


def format_date(val):
    if pd.isna(val) or val == "":
        return ""
    try:
        return pd.to_datetime(val).strftime("%d-%m-%Y")
    except Exception:
        return str(val)

# Q7

def body_part(row):
    matches = set()
    icd = str(row.get("Primary_Diagnosis_Code", ""))
    blob = " ".join([str(row.get(c, "")) for c in ["Diagnosis_Description", "Assessment"]]).lower()
    for cat, prefixes in PREFIX_BUCKETS.items():
        if icd.startswith(prefixes):
            matches.add(cat)
    for cat, kws in KEYWORDS.items():
        if any_kw(blob, kws):
            matches.add(cat)
    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        return "Multiple Areas / Systemic"
    return ""

# Q8

def side(row, part):
    icd = str(row.get("Primary_Diagnosis_Code", ""))
    if len(icd) >= 5 and icd[4] in ICD_LATERALITY:
        return ICD_LATERALITY[icd[4]]
    blob = " ".join([std(row.get(c, "")) for c in ["Diagnosis_Description", "Assessment", "Range_of_Motion", "Strength"]])
    if re.search(r"\bbilat(er(al)?)?\b|\bboth\b", blob):
        return "Bilateral"
    if "left" in blob:
        return "Left"
    if "right" in blob:
        return "Right"
    if part in ["Spine/Trunk", "Head/Face/Jaw"]:
        return "Not Applicable"
    return ""

# Q12

def surgery_type(row):
    if std(row.get("Had_Surgery", "")) not in ("yes", "y", "true", "1"):
        return ""
    blob = " ".join([std(row.get(c, "")) for c in ["Diagnosis_Description", "Assessment", "Justification_for_PT"]])
    for cat, kws in SURGERY_KEYWORDS.items():
        if any_kw(blob, kws):
            return cat
    return "Other Orthopedic/Soft Tissue Surgery"

# Q13

def objective_findings(row):
    findings = []
    rom = std(row.get("Range_of_Motion", ""))
    strength = std(row.get("Strength", ""))
    assessment = std(row.get("Assessment", ""))
    if any(w in rom for w in ["limited", "restriction"]) or "rom" in rom:
        findings.append("Restricted ROM")
    if any(tok in strength for tok in ["/5", "weak", "deficit"]):
        findings.append("Strength Deficits")
    if any(w in assessment for w in ["pain", "tender", "swelling"]):
        findings.append("Pain/Swelling")
    if any(w in assessment for w in ["gait", "balance"]):
        findings.append("Balance/Gait")
    if any(w in assessment for w in SPECIAL_TESTS):
        findings.append("Positive Special Tests")
    return "; ".join(findings)

# Build output

def generate(df):
    rows, anomalies = [], []
    for _, r in df.iterrows():
        part = body_part(r)
        rec = {
            "Patient_ID": r.get("Patient_ID"),
            "Name": r.get("Patient_Name"),
            "DOB": format_date(r.get("Date_of_Birth")),
            "Payer": r.get("Insurance_Payer"),
            "Policy#": r.get("Policy_Number"),
            "Ref_MD": r.get("Referring_Physician"),
            "ICD10": r.get("Primary_Diagnosis_Code"),
            "Body_Part": part,
            "Side": side(r, part),
            "Injury_Date": format_date(r.get("Date_of_Injury_Onset")),
        }
        has_surg = std(r.get("Had_Surgery", "")) in ("yes", "y", "true", "1")
        rec["Had_Surgery"] = "Yes" if has_surg else "No"
        rec["Surgery_Date"] = format_date(r.get("Date_of_Surgery")) if has_surg else ""
        rec["Surgery_Type"] = surgery_type(r)
        rec["Objective_Findings"] = objective_findings(r)
        rows.append(rec)
        # anomaly checks
        if rec["Body_Part"] == "" or rec["Side"] == "":
            anomalies.append(rec)
    return pd.DataFrame(rows), pd.DataFrame(anomalies)

# ──────────────────────────────────────────────────────────────
# Streamlit workflow
# ──────────────────────────────────────────────────────────────

upl = st.file_uploader("Upload patient dataset (CSV or Excel)", type=["csv", "xlsx", "xls"])
if upl:
    try:
        patients = pd.read_csv(upl) if upl.name.endswith(".csv") else pd.read_excel(upl)
        st.success(f"Loaded **{len(patients)}** rows")
        out_df, anomaly_df = generate(patients)

        st.subheader("Generated PA Responses")
        st.dataframe(out_df, use_container_width=True)
        st.download_button("Download CSV", out_df.to_csv(index=False).encode(), "pa_responses.csv", mime="text/csv")

        # Summary visuals
        st.subheader("Summary Visuals")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.write("### Body Part Distribution")
            st.bar_chart(out_df["Body_Part"].value_counts())
        with col2:
            st.write("### Affected Side")
            st.bar_chart(out_df["Side"].value_counts())
        with col3:
            st.write("### Surgery Yes/No")
            st.bar_chart(out_df["Had_Surgery"].value_counts())

        # Anomalies
        st.subheader("Data Anomalies (blank Body Part / Side)")
        if anomaly_df.empty:
            st.success("No anomalies found ✔️")
        else:
            st.warning(f"{len(anomaly_df)} records need review")
            st.dataframe(anomaly_df, use_container_width=True)
    except Exception as e:
        st.error(f"Error: {e}")
else:
    st.info("Awaiting file upload…")
