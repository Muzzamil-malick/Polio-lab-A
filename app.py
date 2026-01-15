############################################
### Polio ES Genetic Linkage Dash App    ###
### (Python conversion — same logic)     ###
############################################

# -----------------------------
# pip install dash dash-bootstrap-components pandas openpyxl plotly kaleido
# -----------------------------

import base64
import io
import re
from datetime import datetime, date

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


# ---- Polio clock constants ----
VP1_LENGTH = 900
CLOCK_RATE_PCT = 1.0
NT_PER_YEAR = VP1_LENGTH * CLOCK_RATE_PCT / 100  # ~9 nt/year


# ---- Round to whole number (2.65 -> 3, 2.45 -> 2) ----
def round_half_up(x):
    # x can be scalar or array-like
    x = pd.to_numeric(x, errors="coerce")
    return np.floor(x + 0.5)


# ---- Helper: extract best match from New_Closest_Match ----
def extract_best_match(text):
    if text is None:
        text = ""
    if (isinstance(text, float) and np.isnan(text)) or str(text).strip() == "":
        return {
            "best_pct": np.nan,
            "best_lab": None,
            "best_dcoll_str": None,
            "best_flask": None,
            "best_location": None,
        }

    lines = str(text).splitlines()

    pattern_header = re.compile(r"^([A-Za-z0-9\+,]+)\(")
    pattern_match = re.compile(
        r"(\d{1,3}(?:\.\d+)?)%\s*with\s*([A-Za-z0-9\-]+).*?\(Dcoll:\s*([0-9]{1,2}-[A-Za-z]{3}-[0-9]{2,4})",
        re.IGNORECASE,
    )
    pattern_loc = re.compile(r"\s([A-Za-z\-]+)\s*\(Dcoll:", re.IGNORECASE)

    current_flask = None

    best_pct = np.nan
    best_lab = None
    best_dcoll_str = None
    best_flask = None
    best_location = None

    for ln in lines:
        h = pattern_header.search(ln)
        if h:
            current_flask = h.group(1)
            continue

        m = pattern_match.search(ln)
        if m:
            pct = float(m.group(1))
            lab = m.group(2)
            dcoll_str = m.group(3)

            locm = pattern_loc.search(ln)
            loc = locm.group(1) if locm else None

            if not np.isnan(pct) and pct < 100:
                if np.isnan(best_pct) or pct > best_pct:
                    best_pct = pct
                    best_lab = lab
                    best_dcoll_str = dcoll_str
                    best_flask = current_flask
                    best_location = loc

    return {
        "best_pct": best_pct,
        "best_lab": best_lab,
        "best_dcoll_str": best_dcoll_str,
        "best_flask": best_flask,
        "best_location": best_location,
    }


def parse_uploaded_file(contents, filename):
    content_type, content_string = contents.split(",", 1)
    decoded = base64.b64decode(content_string)

    ext = filename.split(".")[-1].lower()

    if ext in ("xlsx", "xls"):
        return pd.read_excel(io.BytesIO(decoded))
    elif ext == "csv":
        return pd.read_csv(io.StringIO(decoded.decode("utf-8", errors="replace")))
    else:
        raise ValueError("Unsupported file type.")


def standardize_dates(series):
    # Try ymd first (like lubridate::ymd); fall back to excel numeric origin if all NA
    s = series.copy()

    dt = pd.to_datetime(s, errors="coerce", infer_datetime_format=True)
    if dt.notna().sum() == 0:
        num = pd.to_numeric(s, errors="coerce")
        # Excel origin like R: as.Date(num, origin="1899-12-30")
        dt = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    return dt.dt.date  # keep date component (like Date in R)


def parse_dmy_date(dmy_str_series):
    # Like lubridate::dmy
    return pd.to_datetime(dmy_str_series, errors="coerce", dayfirst=True).dt.date


def build_parsed_df(df, col_date, col_labid, col_closest, col_flask, col_district, col_country):
    df = df.copy()

    # Collection_date_std
    if col_date not in df.columns:
        raise KeyError(f"Collection date column not found: {col_date}")
    df["Collection_date_std"] = standardize_dates(df[col_date])

    # Extract best matches
    if col_closest not in df.columns:
        raise KeyError(f"Closest match column not found: {col_closest}")

    best = [extract_best_match(x) for x in df[col_closest].astype("object").tolist()]
    best_df = pd.DataFrame(best)

    df["closest_pct"] = best_df["best_pct"]
    df["closest_lab"] = best_df["best_lab"]
    df["closest_dcoll_str"] = best_df["best_dcoll_str"]
    df["representative_flask"] = best_df["best_flask"]
    df["closest_location"] = best_df["best_location"]

    df["closest_collection_date"] = parse_dmy_date(df["closest_dcoll_str"])

    # Compute clock features
    cd = pd.to_datetime(df["Collection_date_std"], errors="coerce")
    ccd = pd.to_datetime(df["closest_collection_date"], errors="coerce")

    df["days_diff"] = (cd - ccd).dt.days.astype("float")

    df["divergence_pct"] = 100 - pd.to_numeric(df["closest_pct"], errors="coerce")
    df["estimated_nt_diff_raw"] = (df["divergence_pct"] / 100) * VP1_LENGTH
    df["expected_nt_diff_raw"] = NT_PER_YEAR * (df["days_diff"] / 365)

    df["estimated_nt_diff"] = round_half_up(df["estimated_nt_diff_raw"])
    df["expected_nt_diff"] = round_half_up(df["expected_nt_diff_raw"])

    df["clock_low"] = df["expected_nt_diff"] - 1
    df["clock_high"] = df["expected_nt_diff"] + 1
    df["allowed_nt_diff"] = df["expected_nt_diff"] + 1

    def direct_link_row(r):
        if pd.isna(r["closest_pct"]) or pd.isna(r["closest_collection_date"]) or pd.isna(r["Collection_date_std"]):
            return "unknown"
        if pd.isna(r["days_diff"]):
            return "unknown"
        if r["days_diff"] < 0:
            return "date_inconsistent"
        if pd.notna(r["estimated_nt_diff"]) and pd.notna(r["expected_nt_diff"]):
            if (r["estimated_nt_diff"] >= r["clock_low"]) and (r["estimated_nt_diff"] <= r["clock_high"]):
                return "Yes"
            return "No"
        return "unknown"

    df["direct_link"] = df.apply(direct_link_row, axis=1)
    df["same_virus"] = df["direct_link"]

    def interpretation_row(r):
        if r["same_virus"] == "Yes":
            return "Direct (expected ± 1)"
        if r["same_virus"] == "No":
            return "Not direct (outside clock ± 1)"
        if r["same_virus"] == "unknown":
            return "Unknown (missing data)"
        if r["same_virus"] == "date_inconsistent":
            return "Date inconsistent (closest after sample)"
        return None

    df["interpretation"] = df.apply(interpretation_row, axis=1)

    def link_comment_row(r):
        if r["same_virus"] == "Yes":
            if pd.notna(r["closest_location"]) and pd.notna(r["closest_collection_date"]):
                return (
                    "Direct link (expected±1) with "
                    + str(r["closest_location"])
                    + " virus detected on "
                    + pd.to_datetime(r["closest_collection_date"]).strftime("%d-%b-%Y")
                )
            if pd.notna(r["closest_collection_date"]):
                return (
                    "Direct link (expected±1) with virus detected on "
                    + pd.to_datetime(r["closest_collection_date"]).strftime("%d-%b-%Y")
                )
        return None

    df["link_comment"] = df.apply(link_comment_row, axis=1)

    # Final rounding like R
    df["closest_pct"] = pd.to_numeric(df["closest_pct"], errors="coerce").round(2)
    df["divergence_pct"] = pd.to_numeric(df["divergence_pct"], errors="coerce").round(2)
    df["days_diff"] = pd.to_numeric(df["days_diff"], errors="coerce").round(0)
    df["estimated_nt_diff_raw"] = pd.to_numeric(df["estimated_nt_diff_raw"], errors="coerce").round(6)
    df["expected_nt_diff_raw"] = pd.to_numeric(df["expected_nt_diff_raw"], errors="coerce").round(6)

    return df


def safe_year(series_of_dates):
    dt = pd.to_datetime(series_of_dates, errors="coerce")
    return dt.dt.year


def fig_top10_source_districts(df, dist_col):
    df2 = (
        df[(df["same_virus"] == "Yes") & df["closest_location"].notna() & (df["closest_location"].astype(str).str.strip() != "")]
        .copy()
    )
    if df2.empty:
        return None, pd.DataFrame()

    df2["source_district"] = df2["closest_location"].astype(str).str.strip()
    df2["target_district"] = df2[dist_col].astype(str).str.strip()

    agg = (
        df2.groupby("source_district", dropna=False)
        .apply(
            lambda g: pd.Series(
                {
                    "total_direct_links": len(g),
                    "exported_links": int(
                        (
                            g[dist_col].notna()
                            & (g["target_district"].str.lower() != g["source_district"].str.lower())
                        ).sum()
                    ),
                    "exported_to_districts": int(
                        g.loc[
                            g[dist_col].notna()
                            & (g["target_district"].str.lower() != g["source_district"].str.lower()),
                            "target_district",
                        ].nunique()
                    ),
                }
            )
        )
        .reset_index()
    )
    agg["internal_links"] = agg["total_direct_links"] - agg["exported_links"]
    agg["pct_exported"] = np.where(
        agg["total_direct_links"] > 0,
        (100 * agg["exported_links"] / agg["total_direct_links"]).round(1),
        np.nan,
    )
    agg = agg.sort_values("total_direct_links", ascending=False).head(10)

    fig = px.bar(
        agg.sort_values("total_direct_links"),
        x="total_direct_links",
        y="source_district",
        orientation="h",
        title="Top 10 Source Districts",
        labels={"total_direct_links": "Direct-linked detections", "source_district": "Source district"},
    )
    return fig, agg


def build_sankey(links_df, title="Genetic Flow Between Districts"):
    if links_df is None or links_df.empty:
        return None

    nodes = pd.Index(pd.unique(links_df[["source", "target"]].values.ravel("K"))).tolist()
    node_map = {n: i for i, n in enumerate(nodes)}

    sk_links = pd.DataFrame(
        {
            "source": links_df["source"].map(node_map),
            "target": links_df["target"].map(node_map),
            "value": links_df["value"].astype(int),
        }
    )

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    label=nodes,
                    pad=18,
                    thickness=18,
                ),
                link=dict(
                    source=sk_links["source"],
                    target=sk_links["target"],
                    value=sk_links["value"],
                ),
            )
        ]
    )
    fig.update_layout(title=title, height=750)
    return fig


def build_timeline_sankey(links_df, title="Timeline Sankey (Closest-match Direct Links)"):
    if links_df is None or links_df.empty:
        return None

    # links_df columns: time_bin, time_bin_date, source, target, value, (t_index optional)
    df = links_df.copy()
    df = df.sort_values(["time_bin_date", "source", "target"])

    time_levels = (
        df[["time_bin", "time_bin_date"]].drop_duplicates().sort_values("time_bin_date").reset_index(drop=True)
    )
    time_levels["t_index"] = np.arange(len(time_levels))

    df = df.merge(time_levels, on=["time_bin", "time_bin_date"], how="left")

    # Build nodes per (district, time_bin)
    nodes_src = df[["source", "time_bin", "t_index"]].rename(columns={"source": "district"}).drop_duplicates()
    nodes_tgt = df[["target", "time_bin", "t_index"]].rename(columns={"target": "district"}).drop_duplicates()
    nodes = pd.concat([nodes_src, nodes_tgt], ignore_index=True).drop_duplicates()

    nodes["name"] = nodes["district"].astype(str) + " @ " + nodes["time_bin"].astype(str)
    nodes = nodes.sort_values(["t_index", "district"]).reset_index(drop=True)

    node_map = {n: i for i, n in enumerate(nodes["name"].tolist())}

    df["source_name"] = df["source"].astype(str) + " @ " + df["time_bin"].astype(str)
    df["target_name"] = df["target"].astype(str) + " @ " + df["time_bin"].astype(str)

    df["source_i"] = df["source_name"].map(node_map)
    df["target_i"] = df["target_name"].map(node_map)
    df = df.dropna(subset=["source_i", "target_i"])

    # Force X positions by time bin (columns). Plotly expects 0..1
    nG = max(1, len(time_levels))
    x_positions = {row["time_bin"]: (0 if nG == 1 else row["t_index"] / (nG - 1)) for _, row in time_levels.iterrows()}

    nodes["x"] = nodes["time_bin"].map(x_positions).astype(float)

    # Y positions: distribute within each column
    nodes["rank_in_bin"] = nodes.groupby("time_bin").cumcount()
    max_in_bin = nodes.groupby("time_bin")["rank_in_bin"].transform("max").replace(0, 1)
    nodes["y"] = (nodes["rank_in_bin"] / max_in_bin).astype(float)

    fig = go.Figure(
        data=[
            go.Sankey(
                arrangement="snap",
                node=dict(
                    label=nodes["name"].tolist(),
                    x=nodes["x"].tolist(),
                    y=nodes["y"].tolist(),
                    pad=14,
                    thickness=16,
                ),
                link=dict(
                    source=df["source_i"].astype(int).tolist(),
                    target=df["target_i"].astype(int).tolist(),
                    value=df["value"].astype(int).tolist(),
                ),
            )
        ]
    )
    fig.update_layout(title=title, height=800)
    return fig


# -----------------------------
# Dash App UI
# -----------------------------
app = Dash(__name__, external_stylesheets=[dbc.themes.YETI])
app.title = "Polio ES Genetic Linkage Dashboard"

SIDEBAR_STYLE = {
    "background": "#ffffff",
    "borderRadius": "12px",
    "boxShadow": "0 4px 12px rgba(0,0,0,0.06)",
    "padding": "15px 18px 18px 18px",
    "marginTop": "10px",
}

app.layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": "#f5f7fa", "minHeight": "100vh"},
    children=[
        dcc.Store(id="store-raw-df"),
        dcc.Store(id="store-parsed-df"),
        html.Br(),
        dbc.Row(
            [
                dbc.Col(
                    html.H3(
                        [
                            html.I(className="bi bi-virus", style={"marginRight": "8px"}),
                            "Polio Environmental Surveillance — Genetic Linkage Dashboard",
                        ]
                    ),
                    width=12,
                )
            ]
        ),
        dbc.Row(
            [
                # Sidebar
                dbc.Col(
                    html.Div(
                        style=SIDEBAR_STYLE,
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        [html.I(className="bi bi-file-earmark-medical", style={"marginRight": "8px"}), "Data Ingestion"],
                                        style={"fontWeight": "600", "fontSize": "16px", "marginBottom": "4px"},
                                    ),
                                    html.Div(
                                        "Upload your ES genetic linkage dataset and configure column names if needed.",
                                        style={"fontSize": "12px", "color": "#7f8c8d", "marginBottom": "12px"},
                                    ),
                                ]
                            ),
                            dcc.Upload(
                                id="upload-file",
                                children=html.Div(["Drag & Drop or ", html.A("Select Excel/CSV")]),
                                style={
                                    "width": "100%",
                                    "height": "60px",
                                    "lineHeight": "60px",
                                    "borderWidth": "1px",
                                    "borderStyle": "dashed",
                                    "borderRadius": "10px",
                                    "textAlign": "center",
                                    "background": "#f8f9fa",
                                },
                                multiple=False,
                            ),
                            html.Hr(),
                            html.Details(
                                open=False,
                                children=[
                                    html.Summary([html.I(className="bi bi-sliders", style={"marginRight": "8px"}), "Advanced column settings"]),
                                    html.Br(),
                                    html.Div("Collection date column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_date", type="text", value="Collection date", style={"width": "100%"}),
                                    html.Br(),
                                    html.Br(),
                                    html.Div("Lab ID column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_labid", type="text", value="Lab ID", style={"width": "100%"}),
                                    html.Br(),
                                    html.Br(),
                                    html.Div("Closest match column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_closest", type="text", value="New_Closest_Match", style={"width": "100%"}),
                                    html.Br(),
                                    html.Br(),
                                    html.Div("Flask column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_flask", type="text", value="Flask No.", style={"width": "100%"}),
                                    html.Br(),
                                    html.Br(),
                                    html.Div("District/Town column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_district", type="text", value="District-Town", style={"width": "100%"}),
                                    html.Br(),
                                    html.Br(),
                                    html.Div("Country column", style={"fontWeight": 500}),
                                    dcc.Input(id="col_country", type="text", value="Country", style={"width": "100%"}),
                                ],
                            ),
                            html.Hr(),
                            html.Div(
                                style={"background": "#f8f9fa", "borderRadius": "10px", "padding": "10px 12px", "marginBottom": "12px"},
                                children=[
                                    html.Strong([html.I(className="bi bi-bezier2", style={"marginRight": "8px"}), "Genetic rule:"]),
                                    html.Br(),
                                    html.Div(
                                        "Polio clock ~9 nt/year. Direct (Yes): Whole-number nt_diff must be within expected ± 1.",
                                        style={"fontSize": "13px"},
                                    ),
                                ],
                            ),
                            html.Hr(),
                            dbc.Button(
                                [html.I(className="bi bi-download", style={"marginRight": "8px"}), "Download All Results (CSV)"],
                                id="btn-download-all",
                                color="primary",
                                style={"width": "100%", "borderRadius": "30px", "fontWeight": 600},
                                disabled=True,
                            ),
                            dcc.Download(id="download-all"),
                            html.Div(id="upload-status", style={"marginTop": "10px", "fontSize": "12px", "color": "#7f8c8d"}),
                        ],
                    ),
                    width=3,
                ),
                # Main
                dbc.Col(
                    dbc.Tabs(
                        [
                            dbc.Tab(
                                label="Overview",
                                tab_id="tab-overview",
                                children=[
                                    html.Br(),
                                    html.H4([html.I(className="bi bi-bar-chart", style={"marginRight": "8px"}), "Summary Overview"]),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Div(
                                                    [
                                                        html.Div("Overview year(s)", style={"fontWeight": 500}),
                                                        dcc.Dropdown(id="overview_years", multi=True),
                                                    ]
                                                ),
                                                width=4,
                                            ),
                                            dbc.Col(
                                                html.Div(
                                                    html.Small("Filter summary + top-10 by selected year(s)."),
                                                    style={"paddingTop": "28px"},
                                                ),
                                                width=8,
                                            ),
                                        ]
                                    ),
                                    html.Br(),
                                    html.Div(id="summary-table-wrap"),
                                    html.Pre(id="overview-text", style={"background": "#ffffff", "padding": "10px", "borderRadius": "10px"}),
                                    html.Hr(),
                                    html.H4([html.I(className="bi bi-geo-alt", style={"marginRight": "8px"}), "Top 10 source districts (closest-match direct links)"]),
                                    dcc.Graph(id="plot-source-districts"),
                                    html.Div(id="top10-table-wrap"),
                                    html.Pre(id="top10-text", style={"background": "#ffffff", "padding": "10px", "borderRadius": "10px"}),
                                ],
                            ),
                            dbc.Tab(
                                label="District Analysis",
                                tab_id="tab-district",
                                children=[
                                    html.Br(),
                                    html.H4([html.I(className="bi bi-building", style={"marginRight": "8px"}), "District Linked Virus Analysis"]),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Div(
                                                    [
                                                        html.Div("Select district / town", style={"fontWeight": 500}),
                                                        dcc.Dropdown(id="district_choice"),
                                                    ]
                                                ),
                                                width=6,
                                            ),
                                            dbc.Col(
                                                html.Div(
                                                    [
                                                        html.Div("Filter by year(s)", style={"fontWeight": 500}),
                                                        dcc.Dropdown(id="year_choice", multi=True),
                                                    ]
                                                ),
                                                width=6,
                                            ),
                                        ]
                                    ),
                                    html.Br(),
                                    html.Pre(id="district-summary", style={"background": "#ffffff", "padding": "10px", "borderRadius": "10px"}),
                                    html.Hr(),
                                    html.H4([html.I(className="bi bi-diagram-3", style={"marginRight": "8px"}), "Genetic Timeline"]),
                                    dcc.Graph(id="district-time-plot"),
                                    html.Hr(),
                                    html.H4([html.I(className="bi bi-calendar3", style={"marginRight": "8px"}), "Monthly Trend by Linked Type"]),
                                    dcc.Graph(id="district-monthly-plot"),
                                    html.Hr(),
                                    html.H4([html.I(className="bi bi-info-circle", style={"marginRight": "8px"}), "Linkage Summary (Counts & Percentages)"]),
                                    html.Div(id="district-linkage-table-wrap"),
                                    html.Pre(id="district-linkage-text", style={"background": "#ffffff", "padding": "10px", "borderRadius": "10px"}),
                                    html.Hr(),
                                    html.H5([html.I(className="bi bi-download", style={"marginRight": "8px"}), "Downloads"]),
                                    dbc.Row(
                                        [
                                            dbc.Col(dbc.Button("District CSV", id="btn-download-district-csv", color="secondary", disabled=True), width="auto"),
                                            dbc.Col(dbc.Button("District Plot (PNG)", id="btn-download-district-png", color="secondary", disabled=True), width="auto"),
                                            dbc.Col(dbc.Button("District PDF Report", id="btn-download-district-pdf", color="secondary", disabled=True), width="auto"),
                                        ],
                                        gutter=2,
                                    ),
                                    dcc.Download(id="download-district-csv"),
                                    dcc.Download(id="download-district-png"),
                                    dcc.Download(id="download-district-pdf"),
                                    html.Br(),
                                ],
                            ),
                            dbc.Tab(
                                label="Genetic Flow (Sankey)",
                                tab_id="tab-sankey",
                                children=[
                                    html.Br(),
                                    html.H4([html.I(className="bi bi-share", style={"marginRight": "8px"}), "Genetic Flow Between Districts"]),
                                    html.P("Interactive Sankey diagram showing direct genetic links (same_virus = Yes)."),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Div(
                                                    [html.Div("Country", style={"fontWeight": 500}), dcc.Dropdown(id="sankey_countries", multi=True)]
                                                ),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div(
                                                    [html.Div("Focus district(s)", style={"fontWeight": 500}), dcc.Dropdown(id="sankey_districts", multi=True)]
                                                ),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div([html.Div("Years", style={"fontWeight": 500}), dcc.Dropdown(id="sankey_years", multi=True)]),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div(
                                                    [
                                                        html.Div("Min link count", style={"fontWeight": 500}),
                                                        dcc.Slider(id="sankey_min_value", min=1, max=30, step=1, value=2),
                                                        html.Br(),
                                                        html.Div("Limit nodes", style={"fontWeight": 500}),
                                                        dcc.Dropdown(
                                                            id="sankey_top_nodes",
                                                            options=[{"label": "No limit", "value": "No limit"}]
                                                            + [{"label": str(x), "value": str(x)} for x in [30, 40, 50, 60, 80, 100]],
                                                            value="No limit",
                                                            clearable=False,
                                                        ),
                                                        dbc.Checkbox(id="sankey_hide_self", label="Hide internal (self) links", value=True),
                                                    ]
                                                ),
                                                width=3,
                                            ),
                                        ]
                                    ),
                                    html.Hr(),
                                    dcc.Graph(id="global-sankey"),
                                    html.Div(
                                        [
                                            html.Small("Nodes = Districts  |  Links = Number of genetically linked detections (source to target)"),
                                            html.Br(),
                                            html.Small("Select 'All districts' to see full network, or choose one/more districts to focus on their connections."),
                                            html.Br(),
                                            html.Small("Use Country + Min link count + Limit nodes to declutter."),
                                            html.Br(),
                                            html.Small("Zoom/pan is enabled. You can drag nodes."),
                                        ],
                                        style={"color": "#7f8c8d"},
                                    ),
                                    html.Br(),
                                ],
                            ),
                            dbc.Tab(
                                label="Genetic Flow (Timeline)",
                                tab_id="tab-tsankey",
                                children=[
                                    html.Br(),
                                    html.H4([html.I(className="bi bi-slash-square", style={"marginRight": "8px"}), "Timeline Sankey (Closest-match Direct Links)"]),
                                    html.P("Sankey-like flow with time bins on the X-axis (based on Collection date)."),
                                    dbc.Row(
                                        [
                                            dbc.Col(
                                                html.Div([html.Div("Country", style={"fontWeight": 500}), dcc.Dropdown(id="tsankey_countries", multi=True)]),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div([html.Div("Focus district(s)", style={"fontWeight": 500}), dcc.Dropdown(id="tsankey_districts", multi=True)]),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div([html.Div("Years", style={"fontWeight": 500}), dcc.Dropdown(id="tsankey_years", multi=True)]),
                                                width=3,
                                            ),
                                            dbc.Col(
                                                html.Div(
                                                    [
                                                        html.Div("Time bin", style={"fontWeight": 500}),
                                                        dcc.Dropdown(
                                                            id="tsankey_time_bin",
                                                            options=[
                                                                {"label": "Month", "value": "month"},
                                                                {"label": "Quarter", "value": "quarter"},
                                                                {"label": "Year", "value": "year"},
                                                            ],
                                                            value="month",
                                                            clearable=False,
                                                        ),
                                                        html.Br(),
                                                        html.Div("Min link count", style={"fontWeight": 500}),
                                                        dcc.Slider(id="tsankey_min_value", min=1, max=30, step=1, value=2),
                                                        html.Br(),
                                                        html.Div("Limit nodes", style={"fontWeight": 500}),
                                                        dcc.Dropdown(
                                                            id="tsankey_top_nodes",
                                                            options=[{"label": "No limit", "value": "No limit"}]
                                                            + [{"label": str(x), "value": str(x)} for x in [30, 40, 50, 60, 80, 100]],
                                                            value="No limit",
                                                            clearable=False,
                                                        ),
                                                        dbc.Checkbox(id="tsankey_hide_self", label="Hide internal (self) links", value=True),
                                                    ]
                                                ),
                                                width=3,
                                            ),
                                        ]
                                    ),
                                    html.Hr(),
                                    dcc.Graph(id="timeline-sankey"),
                                    html.Div(
                                        [
                                            html.Small("X-axis = time bins (collection date). Each district repeats per time bin."),
                                            html.Br(),
                                            html.Small("Links = direct closest-match links (same_virus == Yes), counted within each time bin."),
                                            html.Br(),
                                            html.Small("You can zoom/pan and drag nodes."),
                                        ],
                                        style={"color": "#7f8c8d"},
                                    ),
                                    html.Br(),
                                ],
                            ),
                        ],
                        id="tabs",
                        active_tab="tab-overview",
                    ),
                    width=9,
                ),
            ]
        ),
        html.Br(),
    ],
)


# -----------------------------
# Upload -> store raw df
# -----------------------------
@app.callback(
    Output("store-raw-df", "data"),
    Output("upload-status", "children"),
    Input("upload-file", "contents"),
    State("upload-file", "filename"),
    prevent_initial_call=True,
)
def on_upload(contents, filename):
    if not contents or not filename:
        return no_update, no_update
    try:
        df = parse_uploaded_file(contents, filename)
        # store as json (records) + columns
        payload = {"columns": df.columns.tolist(), "data": df.to_dict("records"), "filename": filename}
        return payload, f"Loaded: {filename} | Rows: {len(df):,} | Cols: {df.shape[1]}"
    except Exception as e:
        return None, f"Upload error: {e}"


# -----------------------------
# Build parsed df whenever raw df or column settings change
# -----------------------------
@app.callback(
    Output("store-parsed-df", "data"),
    Output("btn-download-all", "disabled"),
    Output("btn-download-district-csv", "disabled"),
    Output("btn-download-district-png", "disabled"),
    Output("btn-download-district-pdf", "disabled"),
    Input("store-raw-df", "data"),
    Input("col_date", "value"),
    Input("col_labid", "value"),
    Input("col_closest", "value"),
    Input("col_flask", "value"),
    Input("col_district", "value"),
    Input("col_country", "value"),
)
def compute_parsed(raw_payload, col_date, col_labid, col_closest, col_flask, col_district, col_country):
    if not raw_payload:
        return None, True, True, True, True
    try:
        df = pd.DataFrame(raw_payload["data"])
        parsed = build_parsed_df(
            df=df,
            col_date=col_date,
            col_labid=col_labid,
            col_closest=col_closest,
            col_flask=col_flask,
            col_district=col_district,
            col_country=col_country,
        )
        payload = {"data": parsed.to_dict("records"), "columns": parsed.columns.tolist()}
        return payload, False, False, False, False
    except Exception:
        # Keep buttons disabled if parsing fails
        return None, True, True, True, True


def get_parsed_df(store_parsed):
    if not store_parsed:
        return pd.DataFrame()
    return pd.DataFrame(store_parsed["data"])


# -----------------------------
# Overview selectors (years) + outputs
# -----------------------------
@app.callback(
    Output("overview_years", "options"),
    Output("overview_years", "value"),
    Input("store-parsed-df", "data"),
)
def overview_year_options(store_parsed):
    df = get_parsed_df(store_parsed)
    if df.empty:
        return [], []
    yrs = safe_year(df["Collection_date_std"]).dropna().unique().astype(int).tolist()
    yrs = sorted(yrs, reverse=True)
    return [{"label": str(y), "value": int(y)} for y in yrs], yrs


@app.callback(
    Output("summary-table-wrap", "children"),
    Output("overview-text", "children"),
    Output("plot-source-districts", "figure"),
    Output("top10-table-wrap", "children"),
    Output("top10-text", "children"),
    Input("store-parsed-df", "data"),
    Input("overview_years", "value"),
    Input("col_district", "value"),
)
def overview_outputs(store_parsed, overview_years, dist_col):
    df = get_parsed_df(store_parsed)
    if df.empty:
        return (
            html.Div("No data loaded."),
            "",
            go.Figure(),
            html.Div(""),
            "",
        )

    # Filter by selected years (like R: if selection exists)
    if overview_years:
        y = safe_year(df["Collection_date_std"])
        df = df[y.isin(overview_years)].copy()

    # Summary table
    counts = df["same_virus"].fillna("NA").value_counts(dropna=False).reset_index()
    counts.columns = ["Classification", "Count"]

    summary_table = dash_table.DataTable(
        data=counts.to_dict("records"),
        columns=[{"name": c, "id": c} for c in counts.columns],
        style_table={"overflowX": "auto"},
        style_cell={"padding": "6px", "fontFamily": "sans-serif", "fontSize": 13},
    )

    total = len(df)
    yes = int((df["same_virus"] == "Yes").sum())
    no = int((df["same_virus"] == "No").sum())
    unknown = int((df["same_virus"] == "unknown").sum())
    inconsistent = int((df["same_virus"] == "date_inconsistent").sum())

    yrs_txt = ", ".join(map(str, sorted(overview_years))) if overview_years else "All years"
    overview_text = (
        f"Overview years: {yrs_txt}\n"
        f"Total detections: {total}\n"
        f"Direct links (Yes): {yes}\n"
        f"Not direct (No): {no}\n"
        f"Unknown: {unknown}\n"
        f"Date inconsistent: {inconsistent}"
    )

    # Top 10 source districts
    if dist_col not in df.columns:
        fig = go.Figure()
        top10_table = html.Div(f"District column not found: {dist_col}")
        top10_text = ""
        return summary_table, overview_text, fig, top10_table, top10_text

    fig, top10 = fig_top10_source_districts(df, dist_col)
    if fig is None:
        fig = go.Figure()
        top10_table = html.Div("No direct links found (for the selected Overview years).")
        top10_text = "No direct links found (for the selected Overview years)."
    else:
        top10_table = dash_table.DataTable(
            data=top10.to_dict("records"),
            columns=[{"name": c, "id": c} for c in top10.columns],
            style_table={"overflowX": "auto"},
            style_cell={"padding": "6px", "fontFamily": "sans-serif", "fontSize": 13},
        )
        top_row = top10.iloc[0]
        top10_text = (
            f"Top hub: {top_row['source_district']} ({int(top_row['total_direct_links'])} links, "
            f"{int(top_row['exported_links'])} exported to {int(top_row['exported_to_districts'])} districts)"
        )

    return summary_table, overview_text, fig, top10_table, top10_text


# -----------------------------
# District Analysis selectors + outputs
# -----------------------------
@app.callback(
    Output("district_choice", "options"),
    Output("district_choice", "value"),
    Output("year_choice", "options"),
    Output("year_choice", "value"),
    Input("store-parsed-df", "data"),
    Input("col_district", "value"),
)
def district_selectors(store_parsed, dist_col):
    df = get_parsed_df(store_parsed)
    if df.empty or dist_col not in df.columns:
        return [], None, [], []

    districts = sorted([x for x in df[dist_col].dropna().unique().tolist()])
    yrs = safe_year(df["Collection_date_std"]).dropna().unique().astype(int).tolist()
    yrs = sorted(yrs, reverse=True)

    district_opts = [{"label": d, "value": d} for d in districts]
    year_opts = [{"label": str(y), "value": int(y)} for y in yrs]

    district_default = districts[0] if districts else None
    return district_opts, district_default, year_opts, yrs


def build_district_df(df, dist_col, district_choice, year_choice):
    df_d = df[df[dist_col] == district_choice].copy()
    if year_choice:
        y = safe_year(df_d["Collection_date_std"])
        df_d = df_d[y.isin(year_choice)].copy()

    dist = str(district_choice).strip().lower()

    def linkage_type_row(r):
        if r["same_virus"] == "Yes" and isinstance(r.get("closest_location", None), str) and r["closest_location"].strip().lower() == dist:
            return "Internal linked Virus"
        if r["same_virus"] == "Yes":
            return "Linked to other district"
        if r["same_virus"] == "No":
            return "Distinct linked Virus"
        return "Unknown"

    df_d["linkage_type"] = df_d.apply(linkage_type_row, axis=1)
    return df_d


@app.callback(
    Output("district-summary", "children"),
    Output("district-time-plot", "figure"),
    Output("district-monthly-plot", "figure"),
    Output("district-linkage-table-wrap", "children"),
    Output("district-linkage-text", "children"),
    Input("store-parsed-df", "data"),
    Input("col_district", "value"),
    Input("col_labid", "value"),
    Input("district_choice", "value"),
    Input("year_choice", "value"),
)
def district_outputs(store_parsed, dist_col, lab_col, district_choice, year_choice):
    df = get_parsed_df(store_parsed)
    if df.empty or not district_choice or dist_col not in df.columns:
        return "No data.", go.Figure(), go.Figure(), html.Div(""), "No data."

    df_d = build_district_df(df, dist_col, district_choice, year_choice)
    if df_d.empty:
        return "No data.", go.Figure(), go.Figure(), html.Div(""), "No data."

    years_txt = ", ".join(map(str, sorted(year_choice))) if year_choice else "All years"

    internal = int((df_d["linkage_type"] == "Internal linked Virus").sum())
    external = int((df_d["linkage_type"] == "Linked to other district").sum())
    distinct = int((df_d["linkage_type"] == "Distinct linked Virus").sum())
    unk = int((df_d["linkage_type"] == "Unknown").sum())

    district_summary = (
        f"District: {district_choice}\nYears: {years_txt}\n\n"
        f"Total: {len(df_d)}\nInternal: {internal}\nImported: {external}\nDistinct: {distinct}\nUnknown: {unk}"
    )

    # Genetic Timeline plot (estimated_nt_diff vs Collection_date_std)
    df_plot = df_d.copy()
    df_plot["Collection_date_std_dt"] = pd.to_datetime(df_plot["Collection_date_std"], errors="coerce")
    df_plot = df_plot[df_plot["Collection_date_std_dt"].notna()].copy()

    if df_plot.empty:
        time_fig = go.Figure()
    else:
        # Tooltip mirrors R
        if lab_col not in df_plot.columns:
            lab_col = None

        def tooltip_row(r):
            return (
                f"Lab ID: {r.get(lab_col,'') if lab_col else ''}<br>"
                f"Flask: {r.get('representative_flask','')}<br>"
                f"Closest: {r.get('closest_lab','')}<br>"
                f"Location: {r.get('closest_location','')}<br>"
                f"Days diff: {r.get('days_diff','')}<br>"
                f"Estimated nt: {r.get('estimated_nt_diff','')}<br>"
                f"Expected nt: {r.get('expected_nt_diff','')}<br>"
                f"Clock window: [{r.get('clock_low','')}, {r.get('clock_high','')}]<br>"
                f"Direct: {r.get('same_virus','')}<br>"
                f"Type: {r.get('linkage_type','')}"
            )

        df_plot["tooltip"] = df_plot.apply(tooltip_row, axis=1)

        time_fig = px.scatter(
            df_plot,
            x="Collection_date_std_dt",
            y="estimated_nt_diff",
            color="linkage_type",
            hover_data={"tooltip": True},
            title=f"Genetic Timeline — {district_choice}",
            labels={"Collection_date_std_dt": "Collection month", "estimated_nt_diff": "Nucleotide difference (whole number)"},
        )
        time_fig.update_traces(hovertemplate="%{customdata[0]}<extra></extra>")
        # dashed hline at 5
        time_fig.add_hline(y=5, line_dash="dash", line_color="black")
        time_fig.update_layout(xaxis_tickangle=90)

    # Monthly Trend plot
    df_m = df_d.copy()
    df_m["Collection_date_std_dt"] = pd.to_datetime(df_m["Collection_date_std"], errors="coerce")
    df_m = df_m[df_m["Collection_date_std_dt"].notna()].copy()

    if df_m.empty:
        monthly_fig = go.Figure()
    else:
        df_m["month"] = df_m["Collection_date_std_dt"].dt.to_period("M").dt.to_timestamp()
        agg = df_m.groupby(["month", "linkage_type"], as_index=False).size()
        agg = agg.rename(columns={"size": "detections"})

        monthly_fig = px.bar(
            agg,
            x="month",
            y="detections",
            color="linkage_type",
            barmode="stack",
            title=f"Monthly Trend — {district_choice}",
            labels={"month": "Month", "detections": "Detections", "linkage_type": "Linkage type"},
        )
        monthly_fig.update_layout(xaxis_tickangle=90)

    # Linkage Summary table + text
    tab = df_d["linkage_type"].value_counts().reset_index()
    tab.columns = ["Linkage_Type", "Count"]
    tab["Percentage"] = (100 * tab["Count"] / tab["Count"].sum()).round(1)

    linkage_table = dash_table.DataTable(
        data=tab.to_dict("records"),
        columns=[{"name": c, "id": c} for c in tab.columns],
        style_table={"overflowX": "auto"},
        style_cell={"padding": "6px", "fontFamily": "sans-serif", "fontSize": 13},
    )

    total = len(df_d)
    linkage_text = (
        f"Total: {total}\n"
        f"Internal: {internal} ({(100*internal/total if total else 0):.1f}%)\n"
        f"Imported: {external} ({(100*external/total if total else 0):.1f}%)\n"
        f"Distinct: {distinct} ({(100*distinct/total if total else 0):.1f}%)"
    )

    return district_summary, time_fig, monthly_fig, linkage_table, linkage_text


# -----------------------------
# Sankey selectors (country/district/years)
# -----------------------------
@app.callback(
    Output("sankey_countries", "options"),
    Output("sankey_countries", "value"),
    Output("sankey_districts", "options"),
    Output("sankey_districts", "value"),
    Output("sankey_years", "options"),
    Output("sankey_years", "value"),
    Input("store-parsed-df", "data"),
    Input("col_country", "value"),
    Input("col_district", "value"),
)
def sankey_selectors(store_parsed, country_col, dist_col):
    df = get_parsed_df(store_parsed)
    if df.empty:
        return [], [], [], [], [], []

    years = safe_year(df["Collection_date_std"]).dropna().unique().astype(int).tolist()
    years = sorted(years, reverse=True)
    year_opts = [{"label": str(y), "value": int(y)} for y in years]

    # Country
    if country_col in df.columns:
        countries = sorted(df[country_col].dropna().unique().tolist())
    else:
        countries = []
    country_opts = [{"label": "All countries", "value": "All countries"}] + [{"label": c, "value": c} for c in countries]

    # Districts
    if dist_col in df.columns:
        dists = sorted(df[dist_col].dropna().unique().tolist())
    else:
        dists = []
    dist_opts = [{"label": "All districts", "value": "All districts"}] + [{"label": d, "value": d} for d in dists]

    return country_opts, ["All countries"], dist_opts, ["All districts"], year_opts, years


@app.callback(
    Output("global-sankey", "figure"),
    Input("store-parsed-df", "data"),
    Input("col_district", "value"),
    Input("col_country", "value"),
    Input("sankey_years", "value"),
    Input("sankey_countries", "value"),
    Input("sankey_districts", "value"),
    Input("sankey_min_value", "value"),
    Input("sankey_top_nodes", "value"),
    Input("sankey_hide_self", "value"),
)
def sankey_plot(store_parsed, dist_col, country_col, years, countries, focus_districts, min_value, top_nodes, hide_self):
    df = get_parsed_df(store_parsed)
    if df.empty or dist_col not in df.columns or not years:
        return go.Figure()

    # Year filter
    y = safe_year(df["Collection_date_std"])
    df2 = df[y.isin(years)].copy()

    # Country filter
    if countries and "All countries" not in countries and country_col in df2.columns:
        df2 = df2[df2[country_col].isin(countries)].copy()

    # Base rows
    df_s = df2[
        (df2["same_virus"] == "Yes")
        & df2["closest_location"].notna()
        & df2[dist_col].notna()
        & (df2["closest_location"].astype(str).str.strip() != "")
        & (df2[dist_col].astype(str).str.strip() != "")
    ].copy()

    df_s["source"] = df_s["closest_location"].astype(str).str.strip()
    df_s["target"] = df_s[dist_col].astype(str).str.strip()

    # Focus districts
    if focus_districts and "All districts" not in focus_districts:
        sel = [str(x).strip() for x in focus_districts]
        df_s = df_s[df_s["source"].isin(sel) | df_s["target"].isin(sel)].copy()

    links = df_s.groupby(["source", "target"], as_index=False).size().rename(columns={"size": "value"})

    # Hide self
    if hide_self:
        links = links[links["source"] != links["target"]].copy()

    # Min link count
    links = links[links["value"] >= int(min_value or 1)].copy()

    # Optional node limit
    if top_nodes and top_nodes != "No limit":
        top_n = int(top_nodes)
        melted = pd.concat(
            [
                links[["source", "value"]].rename(columns={"source": "name"}),
                links[["target", "value"]].rename(columns={"target": "name"}),
            ],
            ignore_index=True,
        )
        strength = melted.groupby("name", as_index=False)["value"].sum().sort_values("value", ascending=False).head(top_n)
        keep = set(strength["name"].tolist())
        links = links[links["source"].isin(keep) | links["target"].isin(keep)].copy()

    fig = build_sankey(links, title="Genetic Flow Between Districts")
    return fig if fig is not None else go.Figure()


# -----------------------------
# Timeline Sankey selectors + plot
# -----------------------------
@app.callback(
    Output("tsankey_countries", "options"),
    Output("tsankey_countries", "value"),
    Output("tsankey_districts", "options"),
    Output("tsankey_districts", "value"),
    Output("tsankey_years", "options"),
    Output("tsankey_years", "value"),
    Input("store-parsed-df", "data"),
    Input("col_country", "value"),
    Input("col_district", "value"),
)
def tsankey_selectors(store_parsed, country_col, dist_col):
    # Same as sankey selectors
    return sankey_selectors(store_parsed, country_col, dist_col)


def floor_date(dt_series, bin_kind):
    dt = pd.to_datetime(dt_series, errors="coerce")
    if bin_kind == "month":
        return dt.dt.to_period("M").dt.to_timestamp()
    if bin_kind == "quarter":
        return dt.dt.to_period("Q").dt.to_timestamp()
    if bin_kind == "year":
        return dt.dt.to_period("Y").dt.to_timestamp()
    return dt.dt.to_period("M").dt.to_timestamp()


@app.callback(
    Output("timeline-sankey", "figure"),
    Input("store-parsed-df", "data"),
    Input("col_district", "value"),
    Input("col_country", "value"),
    Input("tsankey_years", "value"),
    Input("tsankey_countries", "value"),
    Input("tsankey_districts", "value"),
    Input("tsankey_time_bin", "value"),
    Input("tsankey_min_value", "value"),
    Input("tsankey_top_nodes", "value"),
    Input("tsankey_hide_self", "value"),
)
def timeline_sankey_plot(store_parsed, dist_col, country_col, years, countries, focus_districts, time_bin, min_value, top_nodes, hide_self):
    df = get_parsed_df(store_parsed)
    if df.empty or dist_col not in df.columns or not years:
        return go.Figure()

    # Year filter
    y = safe_year(df["Collection_date_std"])
    df2 = df[y.isin(years)].copy()

    # Country filter
    if countries and "All countries" not in countries and country_col in df2.columns:
        df2 = df2[df2[country_col].isin(countries)].copy()

    # Base rows
    df_s = df2[
        (df2["same_virus"] == "Yes")
        & df2["Collection_date_std"].notna()
        & df2["closest_location"].notna()
        & df2[dist_col].notna()
        & (df2["closest_location"].astype(str).str.strip() != "")
        & (df2[dist_col].astype(str).str.strip() != "")
    ].copy()

    df_s["source"] = df_s["closest_location"].astype(str).str.strip()
    df_s["target"] = df_s[dist_col].astype(str).str.strip()

    # Focus districts
    if focus_districts and "All districts" not in focus_districts:
        sel = [str(x).strip() for x in focus_districts]
        df_s = df_s[df_s["source"].isin(sel) | df_s["target"].isin(sel)].copy()

    # Time bin label/order
    df_s["Collection_date_std_dt"] = pd.to_datetime(df_s["Collection_date_std"], errors="coerce")
    df_s["time_bin_date"] = floor_date(df_s["Collection_date_std_dt"], time_bin)

    if time_bin == "month":
        df_s["time_bin"] = df_s["time_bin_date"].dt.strftime("%Y-%m")
    elif time_bin == "quarter":
        # YYYY-Q#
        q = df_s["time_bin_date"].dt.to_period("Q")
        df_s["time_bin"] = q.astype(str).str.replace("Q", "-Q", regex=False)
    elif time_bin == "year":
        df_s["time_bin"] = df_s["time_bin_date"].dt.strftime("%Y")
    else:
        df_s["time_bin"] = df_s["time_bin_date"].dt.strftime("%Y-%m")

    links = (
        df_s.groupby(["time_bin", "time_bin_date", "source", "target"], as_index=False)
        .size()
        .rename(columns={"size": "value"})
    )

    # Hide self
    if hide_self:
        links = links[links["source"] != links["target"]].copy()

    # Min link count
    links = links[links["value"] >= int(min_value or 1)].copy()

    # Optional district limit (based on total across all time bins)
    if top_nodes and top_nodes != "No limit":
        top_n = int(top_nodes)
        melted = pd.concat(
            [
                links[["source", "value"]].rename(columns={"source": "district"}),
                links[["target", "value"]].rename(columns={"target": "district"}),
            ],
            ignore_index=True,
        )
        strength = (
            melted.groupby("district", as_index=False)["value"].sum()
            .sort_values("value", ascending=False)
            .head(top_n)
        )
        keep = set(strength["district"].tolist())
        links = links[links["source"].isin(keep) | links["target"].isin(keep)].copy()

    fig = build_timeline_sankey(links, title="Timeline Sankey (Closest-match Direct Links)")
    return fig if fig is not None else go.Figure()


# -----------------------------
# Downloads
# -----------------------------
@app.callback(
    Output("download-all", "data"),
    Input("btn-download-all", "n_clicks"),
    State("store-parsed-df", "data"),
    prevent_initial_call=True,
)
def download_all(n, store_parsed):
    df = get_parsed_df(store_parsed)
    if df.empty:
        return no_update
    fname = f"Polio_Results_{date.today().isoformat()}.csv"
    return dcc.send_data_frame(df.to_csv, filename=fname, index=False, na_rep="")


@app.callback(
    Output("download-district-csv", "data"),
    Input("btn-download-district-csv", "n_clicks"),
    State("store-parsed-df", "data"),
    State("col_district", "value"),
    State("district_choice", "value"),
    State("year_choice", "value"),
    prevent_initial_call=True,
)
def download_district_csv(n, store_parsed, dist_col, district_choice, year_choice):
    df = get_parsed_df(store_parsed)
    if df.empty or not district_choice or dist_col not in df.columns:
        return no_update
    df_d = build_district_df(df, dist_col, district_choice, year_choice)
    fname = f"District_{district_choice}_{date.today().isoformat()}.csv"
    return dcc.send_data_frame(df_d.to_csv, filename=fname, index=False, na_rep="")


@app.callback(
    Output("download-district-png", "data"),
    Input("btn-download-district-png", "n_clicks"),
    State("store-parsed-df", "data"),
    State("col_district", "value"),
    State("col_labid", "value"),
    State("district_choice", "value"),
    State("year_choice", "value"),
    prevent_initial_call=True,
)
def download_district_png(n, store_parsed, dist_col, lab_col, district_choice, year_choice):
    # Export the same timeline plot as PNG (like the R webshot)
    df = get_parsed_df(store_parsed)
    if df.empty or not district_choice or dist_col not in df.columns:
        return no_update

    df_d = build_district_df(df, dist_col, district_choice, year_choice)
    df_d["Collection_date_std_dt"] = pd.to_datetime(df_d["Collection_date_std"], errors="coerce")
    df_plot = df_d[df_d["Collection_date_std_dt"].notna()].copy()

    if df_plot.empty:
        return no_update

    fig = px.scatter(
        df_plot,
        x="Collection_date_std_dt",
        y="estimated_nt_diff",
        color="linkage_type",
        title=f"Genetic Timeline — {district_choice}",
    )
    fig.add_hline(y=5, line_dash="dash", line_color="black")
    fig.update_layout(xaxis_tickangle=90)

    png_bytes = fig.to_image(format="png", width=1200, height=800, scale=1)
    fname = f"District_{district_choice}_plot_{date.today().isoformat()}.png"
    return dcc.send_bytes(lambda: png_bytes, filename=fname)


@app.callback(
    Output("download-district-pdf", "data"),
    Input("btn-download-district-pdf", "n_clicks"),
    State("store-parsed-df", "data"),
    State("col_district", "value"),
    State("district_choice", "value"),
    State("year_choice", "value"),
    prevent_initial_call=True,
)
def download_district_pdf(n, store_parsed, dist_col, district_choice, year_choice):
    # Simple PDF report (counts table) like the Rmd placeholder in your R code
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    df = get_parsed_df(store_parsed)
    if df.empty or not district_choice or dist_col not in df.columns:
        return no_update

    df_d = build_district_df(df, dist_col, district_choice, year_choice)

    internal = int((df_d["linkage_type"] == "Internal linked Virus").sum())
    external = int((df_d["linkage_type"] == "Linked to other district").sum())
    distinct = int((df_d["linkage_type"] == "Distinct linked Virus").sum())

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, h - 72, "District Report")
    c.setFont("Helvetica", 12)
    c.drawString(72, h - 100, f"District: {district_choice}")
    c.drawString(72, h - 118, f"Years: {', '.join(map(str, sorted(year_choice))) if year_choice else 'All years'}")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, h - 160, "Summary")
    c.setFont("Helvetica", 12)
    c.drawString(72, h - 180, f"Total: {len(df_d)}")
    c.drawString(72, h - 198, f"Internal: {internal}")
    c.drawString(72, h - 216, f"Imported: {external}")
    c.drawString(72, h - 234, f"Distinct: {distinct}")

    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()
    buf.close()

    fname = f"District_{district_choice}_Report_{date.today().isoformat()}.pdf"
    return dcc.send_bytes(lambda: pdf_bytes, filename=fname)


if __name__ == "__main__":
    # Run: python app.py
    app.run_server(debug=True)

