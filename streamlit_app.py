import io
import sys
import traceback
import pandas as pd
import streamlit as st

APP_TITLE = "FedEx report automation"
PHASE = "Phase 1: Shipment Data + Criteria"

REQUIRED_RAW_COLUMNS = [
    # Minimum columns we rely on for Phase 1 logic
    "Carrier Name",
    "Order Number",
    "Active Equipment ID",
    "Historical Equipment ID",
]

# Helper: read uploaded file into a DataFrame
def read_any_table(uploaded_file, expected_sheet_name=None):
    """
    Reads CSV or Excel. If Excel and expected_sheet_name is provided and present, loads that sheet;
    otherwise loads the first sheet.
    """
    if uploaded_file is None:
        return None, "No file uploaded."
    name = uploaded_file.name.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
            return df, None
        elif name.endswith(".xlsx") or name.endswith(".xls"):
            xls = pd.ExcelFile(uploaded_file)
            sheet_to_read = expected_sheet_name if expected_sheet_name in xls.sheet_names else xls.sheet_names[0]
            df = pd.read_excel(xls, sheet_name=sheet_to_read)
            return df, None
        else:
            return None, "Unsupported file type. Please upload .csv or .xlsx"
    except Exception as e:
        return None, f"Failed to read file: {e}"

def validate_columns(df, needed, label):
    missing = [c for c in needed if c not in df.columns]
    if missing:
        return f"{label} is missing required column(s): {', '.join(missing)}"
    return None

def build_workbook(raw_df: pd.DataFrame, criteria_df: pd.DataFrame) -> bytes:
    """
    Create the Excel workbook in-memory with:
      - Sheet 'Shipment Data' as an Excel Table
      - Sheet 'Criteria' as provided
      - Formulas in AX (Trailer), AY (Network), AZ (LOC)
    """
    # Ensure new columns exist at the END in the requested order
    out_df = raw_df.copy()

    # If any of the three columns already exist, drop them to re-create cleanly
    for col in ["Trailer", "Network", "LOC"]:
        if col in out_df.columns:
            out_df.drop(columns=[col], inplace=True)

    out_df["Trailer"] = ""   # AX
    out_df["Network"] = ""   # AY
    out_df["LOC"] = ""       # AZ

    # Prepare in-memory buffer
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        # Write Criteria first (as provided)
        criteria_df.to_excel(writer, sheet_name="Criteria", index=False)

        # Write Shipment Data (values first; we'll turn it into a table + column formulas)
        out_df.to_excel(writer, sheet_name="Shipment Data", index=False, startrow=0, startcol=0)

        wb  = writer.book
        ws  = writer.sheets["Shipment Data"]

        # Determine table size
        n_rows, n_cols = out_df.shape
        # Excel table range: row0 is header row; table data rows start at row=1
        first_row, first_col = 0, 0
        last_row  = n_rows     # includes header row at 0 + data rows 1..n_rows
        last_col  = n_cols - 1

        # Build table column definitions, adding formulas for the last three columns
        # Use structured references for formulas so the table will auto-fill
        cols_meta = []
        for c in out_df.columns[:-3]:
            cols_meta.append({"header": c})

        # Column formulas (structured references)
        # Trailer (AX) per your rules
        trailer_formula = (
            '=IF(OR(LEFT([@[Active Equipment ID]],6)="861861",'
            'LEFT([@[Active Equipment ID]],5)="86355"),'
            '"FedEx Trailer",'
            'IF(OR(UPPER(TRIM([@[Active Equipment ID]]))="UNKNOWN",'
            'UPPER(TRIM([@[Active Equipment ID]]))="UNKOWN"),'
            'IF(OR(LEFT([@[Historical Equipment ID]],6)="861861",'
            'LEFT([@[Historical Equipment ID]],5)="86355"),'
            '"FedEx Trailer","Subco Trailer"),'
            '"Subco Trailer"))'
        )

        # Network (AY): first 3 chars of Order Number
        network_formula = '=IFERROR(LEFT([@[Order Number]],3),"")'

        # LOC (AZ): VLOOKUP against Criteria!A:D using Carrier Name
        loc_formula = '=IFERROR(VLOOKUP([@[Carrier Name]],Criteria!$A:$D,4,0),"")'

        cols_meta.append({"header": "Trailer", "formula": trailer_formula})
        cols_meta.append({"header": "Network", "formula": network_formula})
        cols_meta.append({"header": "LOC",     "formula": loc_formula})

        # Make an Excel Table so structured refs work
        ws.add_table(
            first_row,
            first_col,
            last_row,
            last_col,
            {
                "name": "ShipmentDataTable",
                "columns": cols_meta,
                "style": {"theme": "Table Style Medium 2", "showFirstColumn": False, "showLastColumn": False},
            },
        )

        # Optional: set column widths for readability
        try:
            # Set some heuristic widths
            width_map = {
                "Carrier Name": 24,
                "Order Number": 18,
                "Active Equipment ID": 20,
                "Historical Equipment ID": 22,
                "Trailer": 16,
                "Network": 10,
                "LOC": 10,
            }
            headers = list(out_df.columns)
            for idx, h in enumerate(headers):
                ws.set_column(idx, idx, width_map.get(h, 14))
        except Exception:
            # Non-fatal
            pass

    output.seek(0)
    return output.getvalue()

def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="📦", layout="centered")
    st.title(APP_TITLE)
    st.caption(PHASE)

    st.markdown("**Inputs required:**")
    col1, col2 = st.columns(2)
    with col1:
        raw_file = st.file_uploader(
            "Upload Raw Export (A:AW) — CSV or Excel",
            type=["csv", "xlsx", "xls"],
            key="raw_upload",
            accept_multiple_files=False
        )
    with col2:
        criteria_file = st.file_uploader(
            "Upload Criteria (with columns A:D)",
            type=["csv", "xlsx", "xls"],
            key="criteria_upload",
            accept_multiple_files=False
        )

    if st.button("Generate Excel", type="primary", use_container_width=True):
        try:
            # Read files
            raw_df, err1 = read_any_table(raw_file)
            criteria_df, err2 = read_any_table(criteria_file, expected_sheet_name="Criteria")

            if err1:
                st.error(f"Raw file error: {err1}")
                return
            if err2:
                st.warning("Note: If your Criteria workbook does not have a 'Criteria' sheet, "
                           "the first sheet was used automatically.")
                # still proceed if criteria_df loaded

            if raw_df is None:
                st.error("Raw file could not be read.")
                return
            if criteria_df is None:
                st.error("Criteria file could not be read.")
                return

            # Validate necessary columns on Raw
            err = validate_columns(raw_df, REQUIRED_RAW_COLUMNS, "Raw export")
            if err:
                st.error(err)
                with st.expander("Columns found in Raw export"):
                    st.write(list(raw_df.columns))
                return

            # Gentle heads-up if raw export already contains more than AW columns (user expectation)
            if len(raw_df.columns) > 49:
                st.info(
                    "Heads up: Raw export contains more than 49 columns (A:AW). "
                    "That's okay; we'll still append Trailer/Network/LOC at the end."
                )

            # Build workbook
            excel_bytes = build_workbook(raw_df, criteria_df)

            st.success("Excel built successfully.")
            st.download_button(
                label="Download Final Excel",
                data=excel_bytes,
                file_name="FedEx_Report_Automation_Phase1.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )

            # Optional previews
            with st.expander("Preview: Shipment Data (first 20 rows)"):
                preview = raw_df.head(20).copy()
                preview["Trailer"] = "«formula»"
                preview["Network"] = "«formula»"
                preview["LOC"] = "«VLOOKUP»"
                st.dataframe(preview, use_container_width=True)

            with st.expander("Preview: Criteria (first 20 rows)"):
                st.dataframe(criteria_df.head(20), use_container_width=True)

        except Exception as e:
            st.error("Something went wrong while generating the Excel.")
            st.code("".join(traceback.format_exception(*sys.exc_info())))

if __name__ == "__main__":
    main()
