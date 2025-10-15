# app.py
import io
import math
import calendar
from datetime import datetime, date
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px

# -------------------------------
# ---------- Utilities ----------
# -------------------------------

def _parse_hhmm(x: str):
    try:
        return datetime.strptime(x.strip(), "%H:%M").time()
    except Exception:
        return None

def calculate_hours_minutes_str(check_in_out: str):
    """
    "09:00-17:30" -> "8h 30m"
    Handles overnight spans by +24h if negative.
    """
    if check_in_out is None or (isinstance(check_in_out, float) and math.isnan(check_in_out)):
        return None
    if not isinstance(check_in_out, str) or not check_in_out.strip():
        return None
    parts = check_in_out.split("-")
    if len(parts) != 2:
        return None
    ci, co = _parse_hhmm(parts[0]), _parse_hhmm(parts[1])
    if ci is None or co is None:
        return None

    base = date(2000, 1, 1)
    ci_dt = datetime.combine(base, ci)
    co_dt = datetime.combine(base, co)
    diff = (co_dt - ci_dt).total_seconds()
    if diff < 0:
        diff += 86400
    if diff <= 0:
        return None

    h = int(diff // 3600)
    m = int(round((diff % 3600) / 60.0))
    if m == 60:
        h += 1
        m = 0
    return f"{h}h {m}m"

def calculate_decimal_hours(check_in_out: str):
    """
    "09:00-17:30" -> 8.5 (float) or np.nan
    """
    if check_in_out is None or (isinstance(check_in_out, float) and math.isnan(check_in_out)):
        return np.nan
    if not isinstance(check_in_out, str) or not check_in_out.strip():
        return np.nan
    parts = check_in_out.split("-")
    if len(parts) != 2:
        return np.nan
    ci, co = _parse_hhmm(parts[0]), _parse_hhmm(parts[1])
    if ci is None or co is None:
        return np.nan

    base = date(2000, 1, 1)
    ci_dt = datetime.combine(base, ci)
    co_dt = datetime.combine(base, co)
    diff = (co_dt - ci_dt).total_seconds()
    if diff < 0:
        diff += 86400
    if diff <= 0:
        return np.nan
    return round(diff / 3600.0, 2)

def month_range(year: int, month: int):
    first = date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    return first, date(year, month, last)

def is_working_day(d: date):
    # Mon–Fri only (to match wday %in% 2:6 intent)
    return d.weekday() < 5

def safe_str(x):
    return "" if pd.isna(x) else str(x)

# -------------------------------
# ---------- Page Setup ---------
# -------------------------------
st.set_page_config(
    page_title="Employee Attendance Dashboard",
    page_icon="📊",
    layout="wide",
)

st.markdown("""
<style>
.reportview-container, .main, .block-container { background-color: #f5f7fa !important; }
.download-btn {
  background: #007bff; color: white; border-radius: 8px; padding: 0.5rem 0.9rem;
  text-decoration: none; font-weight: 600;
}
.download-btn:hover { background: #0056b3; color: white; }
</style>
""", unsafe_allow_html=True)

st.title("Employee Attendance Dashboard")

# -------------------------------
# ---------- Sidebar ------------
# -------------------------------
st.sidebar.header("Upload Files")
attendance_file = st.sidebar.file_uploader("Select Attendance Excel File (.xlsx)", type=["xlsx"])
section_file = st.sidebar.file_uploader("Select Section Details File (.xlsx)", type=["xlsx"])

month = st.sidebar.selectbox("Month:", list(range(1, 12+1)), index=(datetime.now().month-1),
                             format_func=lambda m: calendar.month_name[m])
year = st.sidebar.selectbox("Year:", list(range(2020, 2027)),
                            index=list(range(2020, 2027)).index(min(datetime.now().year, 2026)))

st.success("Upload both Excel files (attendance & section mapping). Then open 'Section Summary' or 'Employee Report'.")

# -------------------------------
# ---------- Data Load ----------
# -------------------------------
@st.cache_data(show_spinner=False)
def load_attendance(file):
    df = pd.read_excel(file, engine="openpyxl")
    df = df.copy()
    cols = list(df.columns)
    if len(cols) >= 1: cols[0] = "Sr.NO"
    if len(cols) >= 2: cols[1] = "First.Name"
    if len(cols) > 2:
        for i in range(2, len(cols)):
            cols[i] = f"X{(i-1):02d}"  # X01..Xnn
    df.columns = cols
    if "First.Name" in df.columns:
        df["First.Name"] = df["First.Name"].astype(str)
    return df

@st.cache_data(show_spinner=False)
def load_sections(file):
    df = pd.read_excel(file, engine="openpyxl")
    df = df.copy()
    if len(df.columns) < 2:
        raise ValueError("Section file must have at least two columns: First.Name, Section")
    df.columns = ["First.Name", "Section"] + list(df.columns[2:])
    df["First.Name"] = df["First.Name"].astype(str)
    return df[["First.Name", "Section"]]

def merge_frames(att_df, sec_df):
    return att_df.merge(sec_df, on="First.Name", how="left")

att_df = load_attendance(attendance_file) if attendance_file else None
sec_df = load_sections(section_file) if section_file else None
merged_df = merge_frames(att_df, sec_df) if (att_df is not None and sec_df is not None) else None

start_date, end_date = month_range(year, month)
days = pd.date_range(start=start_date, end=end_date, freq="D")
day_cols = [f"X{d.day:02d}" for d in days]

# -------------------------------
# ---------- Tabs ---------------
# -------------------------------
tab_upload, tab_summary, tab_employee = st.tabs(["Upload Files", "Section Summary", "Employee Report"])

with tab_upload:
    st.subheader("Files")
    c1, c2 = st.columns(2)
    with c1:
        if att_df is not None:
            st.caption("Attendance (first 10 rows)")
            st.dataframe(att_df.head(10), use_container_width=True)
    with c2:
        if sec_df is not None:
            st.caption("Section Map (first 10 rows)")
            st.dataframe(sec_df.head(10), use_container_width=True)

with tab_summary:
    st.subheader("Section Summary")
    if merged_df is None or merged_df.empty:
        st.info("Upload both files to see Section Summary.")
    else:
        sections = sorted([s for s in merged_df["Section"].dropna().unique()])
        if not sections:
            st.warning("No sections found in mapping.")
        else:
            section_choice = st.selectbox("Choose Section:", sections)
            emp_data = merged_df.loc[merged_df["Section"] == section_choice].copy()

            if emp_data.empty:
                st.warning("No employees in this section for the selected month/year.")
            else:
                working_days = sum(is_working_day(d.date()) for d in days)
                total_working_hours = working_days * 8

                records = []
                for emp in emp_data["First.Name"].unique():
                    row = emp_data.loc[emp_data["First.Name"] == emp].head(1)
                    vals = [safe_str(row[col].values[0]) if col in row.columns else "" for col in day_cols]
                    vals = [v for v in vals if v]
                    hours_vec = [calculate_decimal_hours(v) for v in vals]
                    hours_vec = [h for h in hours_vec if not pd.isna(h)]

                    total_hours = round(sum(hours_vec), 2) if hours_vec else 0.0
                    total_days_present = len(hours_vec)
                    perc = round((total_hours / total_working_hours) * 100, 2) if total_working_hours > 0 else 0.0
                    status = "⚠️ Below Target" if perc < 80 else "✅ Satisfactory"

                    records.append({
                        "Employee": emp,
                        "Days": f"{total_days_present} / {working_days}",
                        "Hours": f"{round(total_hours,1)} / {total_working_hours}",
                        "Percentage_Worked": perc,
                        "Status": status
                    })

                summary_df = pd.DataFrame(records).sort_values("Percentage_Worked", ascending=False)
                st.dataframe(summary_df, use_container_width=True)

                # Bar chart (ggplot-like)
                plot_df = summary_df.sort_values("Percentage_Worked")
                fig = px.bar(plot_df, x="Percentage_Worked", y="Employee",
                             orientation="h", title="Section Attendance Overview",
                             text="Percentage_Worked", color="Percentage_Worked",
                             color_continuous_scale=["#ff6666", "#33cc33"])
                fig.update_traces(texttemplate="%{text:.2f}%", textposition="outside")
                fig.update_layout(xaxis_title="Attendance %", yaxis_title="Employee",
                                  margin=dict(l=10, r=10, t=50, b=10), height=420,
                                  coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True)

with tab_employee:
    st.subheader("Employee Report")
    if merged_df is None or merged_df.empty:
        st.info("Upload both files to see Employee Report.")
    else:
        employees = sorted(merged_df["First.Name"].dropna().unique())
        if not employees:
            st.warning("No employees found.")
        else:
            emp_choice = st.selectbox("Choose Employee:", employees)

            emp_row = merged_df.loc[merged_df["First.Name"] == emp_choice].head(1)
            if emp_row.empty:
                st.warning("No data for this employee.")
            else:
                detailed = pd.DataFrame({"Date": pd.to_datetime(days.date)})
                times, hours_str, dec_hours = [], [], []
                for d in detailed["Date"]:
                    col = f"X{d.day:02d}"
                    val = safe_str(emp_row[col].values[0]) if col in emp_row.columns else ""
                    val = None if (val is None or val.strip() == "") else val
                    times.append(val)
                    hours_str.append(calculate_hours_minutes_str(val))
                    dec_hours.append(calculate_decimal_hours(val))

                detailed["Time"] = times
                detailed["Hours"] = hours_str
                detailed["DecimalHours"] = dec_hours
                detailed["Week"] = detailed["Date"].dt.day.apply(lambda dd: f"Week-{math.ceil(dd/7)}")
                detailed["WorkingDay"] = detailed["Date"].dt.day_name().isin(
                    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                )

                # Weekly summary (Mon–Fri)
                wk = (
                    detailed.loc[detailed["WorkingDay"]]
                    .groupby("Week", as_index=False)
                    .agg(
                        Days_Present=("DecimalHours", lambda s: int(s.notna().sum())),
                        Total_Days=("WorkingDay", "size"),
                        Hours_Worked=("DecimalHours", lambda s: round(float(np.nansum(s)), 1)),
                    )
                    .sort_values("Week", key=lambda s: s.str.extract(r"(\d+)").astype(int)[0])
                )
                wk["Total_Hours"] = wk["Total_Days"] * 8
                wk["Attendance_Percent"] = wk.apply(
                    lambda r: round((r["Hours_Worked"] / r["Total_Hours"]) * 100, 2) if r["Total_Hours"] > 0 else 0.0, axis=1
                )
                wk["Days"] = wk["Days_Present"].astype(str) + " / " + wk["Total_Days"].astype(str)
                wk["Hours"] = wk["Hours_Worked"].astype(str) + " / " + wk["Total_Hours"].astype(str)
                wk_disp = wk[["Week", "Days", "Hours", "Attendance_Percent"]]

                st.markdown("### Weekly Attendance Summary")
                st.dataframe(wk_disp, use_container_width=True)

                st.markdown("### Detailed Daily Attendance")
                show = detailed.copy()
                show["Date"] = show["Date"].dt.date
                st.dataframe(show[["Date", "Time", "Hours", "DecimalHours", "Week"]], use_container_width=True)

                # Download CSV
                out = io.StringIO()
                show.to_csv(out, index=False)
                st.download_button(
                    label="Download Full Employee Report (CSV)",
                    data=out.getvalue().encode("utf-8"),
                    file_name=f"{emp_choice}_{calendar.month_name[month]}_{year}_Report.csv",
                    mime="text/csv",
                    type="primary"
                )
