import sys
import pandas as pd
from datetime import datetime
import re
import sqlite3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
import time
import winsound
import json
import os
from openai import OpenAI
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from collections import Counter
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from SmartDataExtractor import database as db_module
from openpyxl import load_workbook
import threading 

class import_audits_table:
    def __init__(self, db_name: str):
        self.db_name = "SmartDataExtractor/"+db_name
        self.mapping_path = "SmartDataExtractor/mapping-tables.xlsx"
        self.timeout_tries = 3
        self.driver = None
        if not os.path.isfile(self.db_name):
            db_module.create_database(self.db_name)
            print("Database created successfully.")
            raise ValueError("New database is created! Initialize again to load existing database.")
            

    def remove_partial_text(self, full_text: str, partial_text: str, persian: bool):
        if persian:
            # Remove zero-width characters
            full_text = re.sub(r'[\u200B\u200C\u200D\u200E\u200F]', '', full_text)
            pattern = rf'(?:(?<=^)|(?<=[ -_])){re.escape(partial_text)}(?:(?=$)|(?=[ -_]))'
        else:
            pattern = rf'(?<![A-Za-z0-9]){re.escape(partial_text)}(?![A-Za-z0-9])'
        # Search for a match
        match = re.search(pattern, full_text)
        if not match:
            return False, full_text.strip()  # Not found → return unchanged
        # Replace the found text + possible surrounding separators
        # Handle cases where multiple spaces/dashes may remain
        new_text = re.sub(pattern, '', full_text, count=1)
        # Clean up extra spaces/dashes caused by removal
        new_text = re.sub(r'[- ]{2,}', '-', new_text)  # merge double separators
        new_text = re.sub(r'^[- ]+|[- ]+$', '', new_text)  # trim start/end separators
        return True, new_text.strip()

    def get_uncertain_category_type(self, fulltext: str, mapping_sheet: str):
        df = pd.read_excel(self.mapping_path, sheet_name=mapping_sheet)
        uncertain_product, uncertain_type = "",""
        persian = False
        if "persian" in mapping_sheet:
            persian = True
        for index, row in df.iterrows():
            txt = str(row['TEXT']).strip().upper()
            product = str(row['محصول']).strip() if pd.notna(row['محصول']) else None
            type = str(row['نوع']).strip() if pd.notna(row['نوع']) else None
            change_flag, fulltext = self.remove_partial_text(fulltext, txt, persian)
            if change_flag:
                uncertain_product = product.strip() if product else None
                uncertain_type = type.strip() if type else None
                fulltext = fulltext.strip()
        return fulltext, uncertain_product, uncertain_type

    def extract_brand(self, full_text):
        # Approved short brands (<4 chars)
        df = pd.read_excel(self.mapping_path, sheet_name="short_length_brands")
        short_length_brands = df["برند"].dropna().astype(str).str.strip().tolist()
        # Normalize input
        text = full_text.strip()
        # Check: only English letters, digits, spaces, and hyphen allowed
        if not re.fullmatch(r"[A-Za-z0-9\- ‐./&'\"\+()_]+", text):
            return None, full_text  # contains Persian or other chars → no brand extracted
        # Split by hyphen
        for complex in short_length_brands:
            if str(text).upper().startswith(complex.upper()+"-"):
                return complex.upper(), str(text).upper().removeprefix(complex.upper()+"-").strip()
            elif str(text).upper().startswith(complex.upper()+" -"):
                return complex.upper(), str(text).upper().removeprefix(complex.upper()+" -").strip()
            elif str(text).upper().startswith(complex.upper()+" "):
                return complex.upper(), str(text).upper().removeprefix(complex.upper()+" -").strip()
        parts = text.split('-')
        first = parts[0].strip()
        # Condition 1: first part must be only English letters
        if not re.fullmatch(r"[A-Za-z. ]+", first):
            return None, full_text
        # Condition 2: length rules
        if len(first) > 4 or first.upper() in [b.upper() for b in short_length_brands]:
            brand = first
            # Remove the first segment + hyphen
            # Rebuild full_text without the first segment
            updated = '-'.join(parts[1:]).strip()
            return brand, updated
        # No match
        return None, full_text

    @staticmethod
    def has_no_persian(text: str) -> bool:
        pattern = r'[\u0600-\u06FF]'
        return not bool(re.search(pattern, text))

    @staticmethod
    def get_truncated_model(text: str) -> str:
        return re.sub(r'[^A-Za-z0-9]', '', text)
    
    def last_brand_extraction(self, full_text:str):
        full_text = full_text.strip().upper()
        df = pd.read_excel(self.mapping_path, sheet_name="brand-mapping-persian")
        df2 = pd.read_excel(self.mapping_path, sheet_name="persian_brand")
        for index, row in df.iterrows():
            if str(row['TEXT']).strip() in full_text:
                return str(row['محصول']).strip().upper(), full_text.replace(str(row['TEXT']).strip(),"").strip()
        if not self.has_no_persian(full_text):
            for index, row in df2.iterrows():
                if str(row['FA_BRAND']).strip() in full_text:
                    return str(row['EN_BRAND']).strip().upper(), full_text.replace(str(row['FA_BRAND']).strip(),"").strip()
        return None, full_text

    def find_similar_model_id(self, category: str, brand: str, truncated_model: str) -> int:
        truncated_model = truncated_model.upper().strip()
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute("SELECT id, truncated_model FROM Models WHERE category=? AND brand=?",(category, brand))
        rows = cursor.fetchall()
        conn.close()
        similar_id1 = similar_id2 = similar_id3 = similar_id4 = 0
        for row in rows:
            id, model = row
            model = str(model).upper().strip()
            if model==truncated_model:
                return id
            elif truncated_model[:-1]==model:
                similar_id2 = id
            elif truncated_model==model[:-1]:
                similar_id1 = id
            elif len(truncated_model) > 9 and truncated_model[:-2]==model:
                similar_id4 = id
            elif len(truncated_model) > 9 and truncated_model==model[:-2]:
                similar_id3 = id
        result = similar_id1 if similar_id1 else similar_id2 if similar_id2 else similar_id3 if similar_id3 else similar_id4 if similar_id4 else -1
        if result == -1 and category == 'Refrigerator':
            for row in rows:
                id, model = row
                model = str(model).upper().strip()
                if model.endswith('FRZ') or truncated_model.endswith('FRZ'):
                    model = model.removesuffix('FRZ')
                    truncated_model = truncated_model.removesuffix('FRZ')
                elif model.endswith('REF') or truncated_model.endswith('REF'):
                    model = model.removesuffix('REF')
                    truncated_model = truncated_model.removesuffix('REF')
                elif model.endswith('TWIN') or truncated_model.endswith('TWIN'):
                    model = model.removesuffix('TWIN')
                    truncated_model = truncated_model.removesuffix('TWIN')
                if model==truncated_model:
                    return id
                elif truncated_model[:-1]==model:
                    similar_id2 = id
                elif truncated_model==model[:-1]:
                    similar_id1 = id
                elif len(truncated_model) > 9 and truncated_model[:-2]==model:
                    similar_id4 = id
                elif len(truncated_model) > 9 and truncated_model==model[:-2]:
                    similar_id3 = id
            result = similar_id1 if similar_id1 else similar_id2 if similar_id2 else similar_id3 if similar_id3 else similar_id4 if similar_id4 else -1
        return result
    
    def find_similar_models_ids(self, category: str, brand: str, truncated_model: str) -> list[int]:
        result = []
        truncated_model = truncated_model.upper().strip()
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute("SELECT id, truncated_model FROM Models WHERE category=? AND brand=?",(category, brand))
        rows = cursor.fetchall()
        conn.close()
        for row in rows:
            id, model = row
            model = str(model).upper().strip()
            if model==truncated_model:
                result.append(id)
            elif truncated_model==model[:-1]:
                result.append(id)
            elif len(truncated_model) > 8 and truncated_model==model[:-2]:
                result.append(id)
        if len(result) == 0 and category == 'Refrigerator':
            for row in rows:
                id, model = row
                model = str(model).upper().strip()
                if model.endswith('FRZ') or truncated_model.endswith('FRZ'):
                    model = model.removesuffix('FRZ')
                    truncated_model = truncated_model.removesuffix('FRZ')
                elif model.endswith('REF') or truncated_model.endswith('REF'):
                    model = model.removesuffix('REF')
                    truncated_model = truncated_model.removesuffix('REF')
                elif model.endswith('TWIN') or truncated_model.endswith('TWIN'):
                    model = model.removesuffix('TWIN')
                    truncated_model = truncated_model.removesuffix('TWIN')
                if model==truncated_model:
                    result.append(id)
                elif truncated_model==model[:-1]:
                    result.append(id)
                elif len(truncated_model) > 7 and truncated_model==model[:-2]:
                    result.append(id)
        return result

    def findby_(self, audit_id: int, fulltext: str, truncated: str, brand: str, model: str, state: int) -> tuple[int, ...]:
        query, parameters, ids = "", (), ()
        if state == 0:
            query = "SELECT id FROM Audits WHERE (full_text=? OR truncated_text=?) AND id != "+str(audit_id)
            parameters = (fulltext, truncated)
        elif state == 1:
            query = "SELECT id FROM Audits WHERE (full_text=? OR (truncated_text=? AND uncertain_brand=?)) AND id != "+str(audit_id)
            parameters = (fulltext, truncated, brand)
        elif state == 2:
            query = "SELECT id FROM Audits WHERE (full_text=? OR (truncated_text=? AND uncertain_brand=?) OR (brand=? AND model=?)) AND id != "+str(audit_id)
            parameters = (fulltext, truncated, brand, brand, model)
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute(query, parameters)
            ids = cursor.fetchall()
        except Exception as e:
            print("ERROR in findby_ method: ",e)
        finally:
            if conn:
                conn.close()
        if len(ids)>0:
            ids = tuple(id for (id,) in ids)
        return ids

    def update_audits(self, source_id: int, target_id: int):
        query = """SELECT model_id, truncated_text, uncertain_brand, uncertain_category, uncertain_type, search_titles, search_descriptions,
                category, brand, model, state FROM Audits WHERE id = ?"""
        parameter = (source_id,)
        update_query = """UPDATE Audits SET model_id = ?, truncated_text = ?, uncertain_brand = ?, uncertain_category = ? , uncertain_type = ?,
                search_titles = ?, search_descriptions = ?, category = ?, brand = ?, model = ?, state = ? , updated_date = ?
                WHERE id = ?"""
        update_parameters = ()
        try:
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute(query, parameter)
            row = cursor.fetchone()
            update_parameters = row + (current_date, target_id)
            cursor.execute(update_query, update_parameters)
            conn.commit()
            print(f"Audits updated completely from id:{source_id} to id:{target_id}")
        except Exception as e:
            print("Error occured while updating Audits: ", e)
        finally:
            if conn:
                conn.close()

    def update_features(self, source_id: int, target_id: int):
        query = "SELECT model_id, name, value FROM Features WHERE audit_id = ?"
        parameter = (source_id,)
        insert_query = "INSERT INTO Features(audit_id, model_id, name, value) VALUES (? , ? , ? , ?)"
        insert_parameters = ()
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute(query, parameter)
            rows = cursor.fetchall()
            for row in rows:
                insert_parameters = (target_id,) + row
                cursor.execute(insert_query, insert_parameters)
                conn.commit()
            print(f"Features updated completely from id:{source_id} to id:{target_id}")
        except Exception as e:
            print("Error occured while updating Features: ", e)
        finally:
            if conn:
                conn.close()

    def find_best_id(self, ids: tuple[int, ...]) -> int:
        best_result = -1
        query_features = "SELECT name, value FROM Features f JOIN Models m ON f.model_id=m.id JOIN Audits a ON a.model_id = m.id WHERE a.id = ?"
        query_audits = """SELECT model_id, truncated_text, uncertain_brand, uncertain_category,
          uncertain_type, search_titles, search_descriptions, category, brand, model, state 
          FROM Audits WHERE id = ?"""
        if len(ids) == 0:
            return best_result
        # try:
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        count_features = []
        for id in ids:
            parameter = (id,)
            cursor.execute(query_features, parameter)
            rows = cursor.fetchall()
            count = len(rows)
            count_features.append((count, id))
        max_item = max(count_features, key=lambda t: t[0])
        if max_item[0] > 0:
            return max_item[1]
        count_features = []
        for id in ids:
            parameter = (id,)
            cursor.execute(query_audits, parameter)
            row = cursor.fetchone()
            count = sum(1 for x in row if x not in (None, ""))
            count_features.append((count, id))
        max_item = max(count_features, key=lambda t: t[0])
        if max_item[0] > 2:
            return max_item[1]
        # except Exception as e:
        #     print("Error occured in find_best_id method: ", e)
        # finally:
        if conn:
            conn.close()
        return best_result

    def autocomplete_by_history(self, audit_id: int, fulltext: str, truncated_text: str, brand: str, model: str, state: int):
        ids = self.findby_(audit_id, fulltext, truncated_text, brand, model, state)
        if len(ids) > 0:
            id = self.find_best_id(ids)
            if id != -1:
                self.update_audits(id, audit_id)
                # self.update_features(id, audit_id)

    def find_unification_model_for(self, category: str, brand: str, model:str):
        models_unification = pd.read_excel(self.mapping_path, sheet_name="models_unification")
        for _, row in models_unification.iterrows():
            if row['brand'].strip().upper() == brand.strip().upper() and row['category'].strip().upper() == category.strip().upper():
                models = [m.strip().upper() for m in row['models'].split(",")]
                for m in models:
                    if m in model:
                        return m, row['unified_model'].strip().upper()
        return None, None

    def find_similar_model_id_based_on_unified(self, category: str, brand: str, model:str):
        _, model_ = self.find_unification_model_for(category, brand, model)
        model_ = model_ if model_ else model
        if model_ == model and (model_.endswith('REF') or model_.endswith('FRZ') or model_.endswith('TWIN')):
            model_ = re.sub(r'(REF|FRZ|TWIN)$', '', model_).removesuffix('-').strip()
        if len(model) > 4 and (model_[-2] == '-' or model_[-3] == '-'):
            model_ = model_[:-2] if model_[-2] == '-' else model_[:-3]
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM Models WHERE category=? AND brand=? AND (unified_model LIKE ? OR model LIKE ?)',(category, brand, f"{model_}%", f"{model_}%"))
        row = cursor.fetchone()
        if conn:
            conn.close()
        if row:
            return row[0]
        return -1


    def import_from_file_to_database(self, file_name: str, sheet_name: str):
        if not file_name and not sheet_name:
            # create sample empty excel file with headers here
            sample_file_path = "SmartDataExtractor/sample_import_file.xlsx"
            columns = ['کالا', 'کد مدل', 'محصول', 'برند', 'ظرفیت', 'نوع', 'تکنولوژی', 'SMART/NON']
            df_sample = pd.DataFrame(columns=columns)
            df_sample.loc[len(df_sample)] = ["GREE-AC-Spilit-Q4MATIC-P12C3",None,None,None,None,None,None,"NOT-SMART"]
            df_sample.loc[len(df_sample)] = ["اسپلیت داخلی 30هزارT3-UV(سردوگرم) اف آر","FR/AC-30SH132",None,"FR",None,None,None,None]
            df_sample.loc[len(df_sample)] = ["BOSCH-WASHER-DW-SMS88TI02M",None,None,None,14,"Free-standing","Non-inverter",None]
            df_sample.loc[len(df_sample)] = ["LG-DW-DFB325HS",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["AKHAVAN-Cooking-GC-M12EDTR",None,"Gas Oven",None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["ALTON-GC-MX5S",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["اجاق گاز5شعلهDGC5-2102nدوو",None,None,None,5,None,None,"NOT-SMART"]
            df_sample.loc[len(df_sample)] = ["SMAGEN-MWO-MF842",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["SHARP-SDA-MWO-R77",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["SAMSUNG-MW-MG40J5133AT",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["مایکروویو اسماگن - مدل MF 932 مشکی","MF932","Microwave","SMAGEN",32,"Countertop","Solardom","SMART"]
            df_sample.loc[len(df_sample)] = ["ARS3Di-L300-S ساید بای ساید",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["BANDINI-F&F-REF-6FOOT",None,None,None,None,"SD-Mini Bar","NonInverter-Defrost",None]
            df_sample.loc[len(df_sample)] = ["BARFAB-REF-3070-TMF",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["BENESS-F&F-FRZ-POLARIS",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["DAEWOO-DLF-2032GW-FRZ",None,None,None,361,None,None,None]
            df_sample.loc[len(df_sample)] = ["یخچال فریزر پایین 24 فوت بلنتون 2011W",None,None,None,505,None,"Inverter","NOT-SMART"]
            df_sample.loc[len(df_sample)] = ["AIWA-AV-TV-65ZQ",None,None,None,None,None,"QLED",None]
            df_sample.loc[len(df_sample)] = ["BOST-TV-32BN3080KM",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["تلویزیون SLD-40NY13400",None,None,None,40,None,None,None]
            df_sample.loc[len(df_sample)] = ["AEG-SDA-VC-VX6-1-OKO",None,"Vacuum Cleaner",None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["BOSCH-VC-BGL8PRO4IR",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["جارو شارژی ایستاده دلمونتی DL380",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["BOSCH-WASHER-WM-WAW2560GC",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["TCL-WM-M94-ASBL",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["لباسشویی  DWK-7200",None,None,None,None,None,None,None]
            df_sample.loc[len(df_sample)] = ["لباسشویی مینی واش تک شو 2.5KG SW25 فریدولین",None,None,None,None,None,None,None]
            df_sample.to_excel(sample_file_path, index=False)
            print(f"Sample file created at: {sample_file_path}")
            return
        if not (file_name.endswith('.xlsx') or file_name.endswith('.xls')):
            raise ValueError("Please provide an Excel file with .xlsx or .xls")
        self.file_path = "SmartDataExtractor/"+file_name
        df = pd.read_excel(self.file_path, sheet_name=sheet_name).drop_duplicates(subset='کالا')
        count = 0
        length = len(df)
        for index, row in df.iterrows():
            count += 1
            last_Audits_id = -1
            print(f"processing for row: {count}/{length}...")
            full_text = str(row['کالا'])
            if not str(full_text).strip():
                continue
            fulltext = full_text.strip().upper()
            df_mismatched = pd.read_excel(self.mapping_path, sheet_name="mismatched")
            for index_m, row_m in df_mismatched.iterrows():
                fulltext = fulltext.replace(row_m['wrong'], row_m['correct'])
            codemodel = str(row['کد مدل']).strip().upper() if pd.notna(row['کد مدل']) and str(row['کد مدل']).strip() else None
            product = str(row['محصول']).strip() if pd.notna(row['محصول']) and row['محصول'] else None
            brand = str(row['برند']).strip().upper() if pd.notna(row['برند']) and row['برند'] else None
            capacity = str(row['ظرفیت']) if pd.notna(row['ظرفیت']) and row['ظرفیت'] else None
            type = str(row['نوع']).strip() if pd.notna(row['نوع']) and row['نوع'] else None
            technology = str(row['تکنولوژی']).strip() if pd.notna(row['تکنولوژی']) and row['تکنولوژی'] else None
            smart = str(row['SMART/NON']).strip().upper() if pd.notna(row['SMART/NON']) and row['SMART/NON'] else None
            truncated_text, uncertain_product, uncertain_type = self.get_uncertain_category_type(fulltext,"mapping")
            if not uncertain_product:
                truncated_text, uncertain_product, uncertain_type = self.get_uncertain_category_type(truncated_text.strip().upper(),"mapping-persian")
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            uncertain_brand, truncated_text = self.extract_brand(truncated_text)
            if not uncertain_brand:
                truncated_text, uncertain_brand, _ = self.get_uncertain_category_type(truncated_text.strip().upper(),"brand-mapping-persian")
            if not uncertain_brand:
                uncertain_brand, truncated_text = self.last_brand_extraction(truncated_text)
            if uncertain_brand or uncertain_product:
                truncated_text = truncated_text.replace('مدل ', '').replace(' مدل', '').replace('مدل', '').strip()
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            state = 0
            if brand and product and codemodel:
                state = 2
                if capacity or type or technology or smart:
                    state = 3
            elif uncertain_brand and uncertain_product and uncertain_brand.strip() and uncertain_product.strip():
                state = 1
                if self.has_no_persian(truncated_text):
                    state = 2
            else:
                state = 0
            truncated_text = truncated_text.upper().strip()
            if (product and product=='Refrigerator') or (not product and uncertain_product and uncertain_product=='Refrigerator'):
                if codemodel:
                    if codemodel.upper().endswith('-BMF'):
                        codemodel = codemodel.upper().removesuffix('-BMF')
                    if codemodel.upper().endswith('BMF'):
                        codemodel = codemodel.upper().removesuffix('BMF')
                    if codemodel.upper().endswith('-TMF'):
                        codemodel = codemodel.upper().removesuffix('-TMF')
                    if codemodel.upper().endswith('TMF'):
                        codemodel = codemodel.upper().removesuffix('TMF')
                    if codemodel.upper().endswith('-SBS'):
                        codemodel = codemodel.upper().removesuffix('-SBS')
                    if codemodel.upper().endswith('SBS'):
                        codemodel = codemodel.upper().removesuffix('SBS')
                if state>=2 and uncertain_product and uncertain_brand and self.has_no_persian(truncated_text):
                    if truncated_text.upper().endswith('-BMF'):
                        truncated_text = truncated_text.upper().removesuffix('-BMF')
                    if truncated_text.upper().endswith('BMF'):
                        truncated_text = truncated_text.upper().removesuffix('BMF')
                    if truncated_text.upper().endswith('-TMF'):
                        truncated_text = truncated_text.upper().removesuffix('-TMF')
                    if truncated_text.upper().endswith('TMF'):
                        truncated_text = truncated_text.upper().removesuffix('TMF')
                    if truncated_text.upper().endswith('-SBS'):
                        truncated_text = truncated_text.upper().removesuffix('-SBS')
                    if truncated_text.upper().endswith('SBS'):
                        truncated_text = truncated_text.upper().removesuffix('SBS')

                if uncertain_type and uncertain_type == 'SD-Frz':
                    suffix = '-FRZ'
                    check_suffixes = ('-FRZ', '-Frz', 'FRZ')
                elif uncertain_type and uncertain_type == 'SD-Ref':
                    suffix = '-REF'
                    check_suffixes = ('-REF', '-Ref', 'REF')
                elif uncertain_type and uncertain_type == 'TWIN':
                    suffix = '-TWIN'
                    check_suffixes = ('-TWIN', '-Twin', 'TWIN')
                else:
                    suffix = ''
                if suffix and codemodel and not any(codemodel.upper().endswith(s.upper()) for s in check_suffixes):
                    codemodel = codemodel + suffix
                if suffix and state>=2 and uncertain_product and uncertain_brand and self.has_no_persian(truncated_text) and not any(truncated_text.upper().endswith(s.upper()) for s in check_suffixes):
                    truncated_text = truncated_text + suffix
            if product and product.lower().strip()=='Air Cooling':
                product = 'Air Conditioner'
            try:
                ff = True
                m_id = mm_id = um_id = -1
                state_ = state
                state__ = 0
                features_ = features__ = []
                type_ = type__ = capacity_ = capacity__ = technology_ = technology__ = smart_ = smart__ = None
                try:
                    cursor.execute(
                        """
                        INSERT INTO Audits (full_text, updated_date, truncated_text, uncertain_brand, uncertain_category, uncertain_type, category, brand, model, state)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (full_text, current_date, truncated_text, uncertain_brand if uncertain_brand else None, uncertain_product if uncertain_product else None, uncertain_type if uncertain_type else None
                            , product if product else uncertain_product if state>=2 else None, brand if brand else uncertain_brand if state>=2 else None, codemodel if codemodel else truncated_text if state>=2 else None, state)
                        )
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    # raise e
                    cursor.execute("SELECT id, model_id FROM Audits WHERE full_text = ?",(full_text,))
                    rr = cursor.fetchone()
                    last_Audits_id = rr[0]
                    mm_id = rr[1] if rr[1] else -1
                    ff = False
                if ff:
                    last_Audits_id = cursor.lastrowid
                if state >= 2:
                    m_category = product if product else uncertain_product
                    m_brand = brand if brand else uncertain_brand
                    m_model = codemodel if codemodel else truncated_text
                    m_model = m_model.replace('- ','-').replace(' -','-').replace('/ ','/').replace(' /','/').replace(' (','(').replace(') ',')')
                    m_model = m_model.replace('- ','-').replace(' -','-').replace('/ ','/').replace(' /','/').replace(' (','(').replace(') ',')').replace(' ','-').replace('_','-')
                    m_truncated = self.get_truncated_model(m_model)
                    m_id = self.find_similar_model_id(m_category, m_brand, m_truncated)
                    um_id = self.find_similar_model_id_based_on_unified(m_category, m_brand, m_model)
                    m_, m_unified = self.find_unification_model_for(m_category, m_brand, m_model)
                    if m_unified:
                        m_unified = self.get_correct_unified_for_(m_model, m_category, m_, m_unified)
                    if m_id == -1 and mm_id == -1:
                        cursor.execute("INSERT INTO Models (category, brand, model, truncated_model, unified_model) VALUES (?, ?, ?, ?, ?)",
                            (m_category, m_brand, m_model, m_truncated, m_unified))
                        conn.commit()
                        m_id = cursor.lastrowid
                    m_id = m_id if mm_id == -1 else mm_id
                    cursor.execute("SELECT name, value FROM Features WHERE model_id = ?",(m_id,))
                    features_ = cursor.fetchall()
                    if len(features_) > 0:
                        state_ = 3
                        for f_ in features_:
                            name , value = f_
                            if name == 'type':
                                type_ = value
                            elif name == 'capacity':
                                capacity_ = value
                            elif name == 'technology':
                                technology_ = value
                            elif name == 'smart':
                                smart_ = value
                    if um_id != -1:
                        cursor.execute("SELECT name, value FROM Features WHERE model_id = ?",(um_id,))
                        features__ = cursor.fetchall()
                        if len(features__) > 0:
                            state__ = 3
                            for f__ in features__:
                                name , value = f__
                                if name == 'type':
                                    type__ = value
                                elif name == 'capacity':
                                    capacity__ = value
                                elif name == 'technology':
                                    technology__ = value
                                elif name == 'smart':
                                    smart__ = value
                    cursor.execute("UPDATE Audits SET model_id = ? , state = ? , updated_date = ? WHERE id = ?",(m_id, state_, current_date, last_Audits_id))
                    conn.commit()
                permission = True
                t = type if type else uncertain_type if uncertain_type else type_ if type_ else None
                if t and type__ and t.strip().upper() != type__.strip().upper():
                    permission = False
                capacity = capacity if capacity else capacity_ if capacity_ else capacity__ if (capacity__ and  permission) else None
                _type = type
                type = type if type else uncertain_type if uncertain_type else type_ if type_ else type__ if type__ else None
                technology = technology if technology else technology_ if technology_ else technology__ if technology__ else None
                smart = smart if smart else smart_ if smart_ else smart__ if smart__ else None
                if m_id and m_id != -1 and state >= 2:
                    rows = []
                    if capacity and capacity.strip():
                        rows.append((last_Audits_id, m_id, "capacity", capacity))
                    if type and type.strip():
                        rows.append((last_Audits_id, m_id, "type", type))
                    if technology and technology.strip():
                        rows.append((last_Audits_id, m_id, "technology", technology))
                    if smart and smart.strip():
                        rows.append((last_Audits_id, m_id, "smart", smart))
                    if len(rows)>0:
                        for row_ in rows:
                            try:
                                cursor.execute("INSERT INTO Features (audit_id, model_id, name, value) VALUES (?, ?, ?, ?)",row_)
                                conn.commit()
                            except sqlite3.IntegrityError:
                                if not (row_[2]=='type' and uncertain_type and not _type):
                                    cursor.execute("UPDATE Features SET value = ? WHERE model_id = ? AND name = ?",(row_[3], row_[1], row_[2]))
                                    conn.commit()
                            except Exception:
                                pass
                        state = 3
                        cursor.execute("UPDATE Audits SET state = ? , updated_date = ? WHERE id = ?",(state, current_date, last_Audits_id))
                        conn.commit()
            except Exception as e:
                print("Error While Importing From File: "+str(e))
            finally:
                if conn:
                    conn.close()
            if state < 3 and last_Audits_id != -1:
                if state == 0:
                    self.autocomplete_by_history(last_Audits_id, full_text, truncated_text, None, None, state)
                elif state == 1:
                    self.autocomplete_by_history(last_Audits_id, full_text, truncated_text,uncertain_brand, None, state)
                elif state == 2 and not (product in ("Vacuum Cleaner","Microwave") or uncertain_product in ("Vacuum Cleaner","Microwave")):
                    self.autocomplete_by_history(last_Audits_id, full_text, truncated_text, brand if brand else uncertain_brand, codemodel if codemodel else truncated_text, state)
        print("rows added completely.")

    def get_persian_category(self, en_category: str):
        if not en_category:
            return ""
        df = pd.read_excel(self.mapping_path, sheet_name="persian_cat")
        for index, row in df.iterrows():
            if str(row['EN_CAT']).upper().strip() == en_category.upper().strip():
                return str(row['FA_CAT']).upper().strip()
        return en_category.upper().strip()
    
    def get_persian_brand(self, en_brand: str):
        if not en_brand:
            return ""
        df = pd.read_excel(self.mapping_path, sheet_name="persian_brand")
        for index, row in df.iterrows():
            if str(row['EN_BRAND']).upper().strip() == en_brand.upper().strip():
                return str(row['FA_BRAND']).upper().strip()
        return en_brand.upper().strip()

    def load_page_and_check(self, url):
        try:
            self.driver.get(url)
        except Exception as e:
            print(f"Page load timeout or error.")
            # return False
        try:
            page_source = self.driver.page_source
            if len(page_source) < 1000:
                print(f"Page failed to load properly with len {len(page_source)}")
                return False
        except Exception as e:
            pass
        try:
            body = self.driver.find_element(By.XPATH, "//body/div[@id='content']/div[@id='main-frame-error']/div[@id='main-content']/div[@id='main-message']/h1")
            if body:
                return False
        except Exception as e:
            pass
        return True


    @staticmethod
    def get_engine_XPATH_by(engine):
        match engine:
            case 'google.com':
                return '//a/h3'
            case 'yahoo.com':
                return '//h3/a'
            case 'bing.com':
                return '//h2/a'
            case 'yandex.com':
                return '(//a/h2)/..'
            case _:
                return ' '
            
    @staticmethod
    def get_alternative_engine_XPATH_by(engine):
        match engine:
            case 'google.com':
                return '//a/h3'
            case 'yahoo.com':
                return '(//a/h3)/..'
            case 'bing.com':
                print('alternative XPATH for bing.com')
                return '//h2[1]/ancestor::a[1]'
            case 'yandex.com':
                return '(//a/h2)/..'
            case _:
                return ' '
            
    @staticmethod
    def get_class_description_by(engine):
        match engine:
            case 'google.com':
                return "div.VwiC3b.yXK7lf.p4wth.r025kc.hJNv6b.Hdw6tb"
            case 'yahoo.com':
                return "div.compText.aAbs p"
            case 'bing.com':
                return 'li.b_algo p'
            case 'yandex.com':
                return ' '
            case _:
                return ' '

    @staticmethod
    def get_alternative_class_description_by(engine):
        match engine:
            case 'google.com':
                return "div.VwiC3b.yXK7lf.p4wth.r025kc.Hdw6tb"
            case 'yahoo.com':
                return "div.compText.aAbs p"
            case 'bing.com':
                return 'li.b_algo p'
            case 'yandex.com':
                return ' '
            case _:
                return ' '


    def update_title_description(self, id, title: str, description: str, state: int):
        title = title.strip()
        description = description.strip()
        if not title or not description:
            print("title or description is empty!")
            return False
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
        sql_query = """UPDATE Audits SET search_titles = ? , search_descriptions = ? , updated_date = ?
                    WHERE """+("id = ?" if state==1 else "model_id = ?")
        parameters = (title, description, current_date, id)
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute(sql_query, parameters)
            conn.commit()
            conn.close()
            print(f"Successfully added title:len {len(title)} and description:len {len(description)} for id: {id}")
            return True
        except Exception:
            print(f"error while inserting title and description! with {title}, {description}, id: {id}")
            return False

    @staticmethod
    def remove_single_char_number_lines(text: str) -> str:
        result_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            # Remove the line only if it is exactly one char AND that char is digit or '.'
            if len(stripped) == 1 and (stripped.isdigit() or stripped == '.'):
                continue
            result_lines.append(line)
        return "\n".join(result_lines)

    def get_type_by(self, model_id: int, state: int) -> str:
        conn = None
        try:
            if state == 1:
                conn = sqlite3.connect(self.db_name, timeout=30)
                cursor = conn.cursor()
                query = "SELECT a.uncertain_type FROM Audits a WHERE a.id = ? ;"
                cursor.execute(query, (model_id,))
                rows = cursor.fetchall()
                if rows and len(rows)>0:
                    rows = [row for (row,) in rows]
                    counter = Counter(rows)
                    return counter.most_common(1)[0][0]
                else:
                    return ""
            else:
                conn = sqlite3.connect(self.db_name, timeout=30)
                cursor = conn.cursor()
                query1 = "SELECT f.value FROM Features f WHERE f.model_id = ? AND f.name = 'type';"
                query2 = "SELECT a.uncertain_type FROM Audits a WHERE a.model_id = ? ;"
                cursor.execute(query1, (model_id,))
                row = cursor.fetchone()
                if row and str(row[0]).strip():
                    return row[0]
                else:
                    cursor.execute(query2, (model_id,))
                    rows = cursor.fetchall()
                    if rows and len(rows)>0:
                        rows = [row for (row,) in rows]
                        counter = Counter(rows)
                        return counter.most_common(1)[0][0]
                    else:
                        return ""
        except Exception as e:
            print("ERROR IN METHOD get_type_by(model_id)")
            return ""
        finally:
            if conn:
                conn.close()


    def extract_engine_search(self, state: int, feature=""):
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        sql_query = ""
        if state==1:
            sql_query = """
                SELECT id, truncated_text, uncertain_brand, uncertain_category FROM Audits
                WHERE state<2 AND (search_titles IS NULL OR search_descriptions IS NULL OR search_titles='' OR search_descriptions='');"""
        elif state==2:
            # sql_query = """SELECT m.id, m.model, m.brand, m.category
            #     FROM Models m
            #     WHERE NOT EXISTS (SELECT 1 FROM Features f WHERE f.model_id = m.id)
            #     AND NOT EXISTS (SELECT 1 FROM Audits a WHERE a.model_id = m.id
            #     AND a.search_descriptions IS NOT NULL AND a.search_descriptions != '');"""
            sql_query = """SELECT DISTINCT m.id, m.model, m.brand, m.category FROM Models m
                JOIN Audits a ON a.model_id = m.id
                LEFT JOIN Features f ON f.model_id = m.id
                WHERE (a.search_descriptions IS NULL OR a.search_descriptions = '')
                GROUP BY m.id
                HAVING SUM(CASE WHEN f.name = 'capacity' THEN 1 ELSE 0 END) = 0
                OR SUM(CASE WHEN f.name = 'type' THEN 1 ELSE 0 END) = 0
                OR SUM(CASE WHEN f.name = 'technology' THEN 1 ELSE 0 END) = 0
                OR SUM(CASE WHEN f.name = 'smart' THEN 1 ELSE 0 END) = 0;"""
        elif state==3:
            sql_query = """SELECT m.id, m.model, m.brand, m.category FROM Models m
                JOIN Audits a ON a.model_id = m.id
                LEFT JOIN Features f ON f.model_id = m.id AND f.name = '"""+feature+"""'
                WHERE a.search_descriptions IS NOT NULL AND a.search_descriptions != '' AND f.id IS NULL
                GROUP BY m.id;"""
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        conn.close()
        print(f"Number Of Models: {len(rows)}")
        if len(rows) == 0:
            print("There is no more row to be processed! end.")
            return
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.driver.set_page_load_timeout(8)
        time.sleep(1)
        all_count = len(rows)
        this_count = 0
        for row in rows:
            this_count += 1
            print(f"Proccessing row {this_count}/{all_count}:")
            fault = 0
            if fault > 5:
                print("Automation failed due to 5 unsuccessful attempts! end.")
                self.driver.quit()
                return
            id, truncated_text, uncertain_brand, uncertain_category = row
            persian_brand = self.get_persian_brand(uncertain_brand) + " "
            uncertain_type = self.get_type_by(id, state)
            category_type = self.get_persian_type_by_category(uncertain_category, uncertain_type) + " "
            search_prompt = (category_type if category_type.strip() else "") + (persian_brand if persian_brand.strip() else "") + ((uncertain_brand+" مدل ") if uncertain_brand else "") + truncated_text
            feature_definition = (self.get_exclusive_feature_definition_by(feature, uncertain_category)+" ") if state==3 else ""
            search_prompt = feature_definition + search_prompt
            google_url = 'https://www.google.com/search?q=' + search_prompt.strip()
            bing_url = 'https://www.bing.com/search?q=' + search_prompt.strip()
            google_enabled = False
            bing_enabled = False
            if self.load_page_and_check(google_url):
                google_enabled = True
                bing_enabled = False
            elif self.load_page_and_check(bing_url):
                google_enabled = False
                bing_enabled = True
            else:
                print(f"failed while loading search engine prompt: {search_prompt}")
                fault += 1
                continue
            time.sleep(1)
            # reCAPTCHA handling
            try:
                if google_enabled and 'google.com/sorry' in self.driver.current_url:
                    self.timeout_tries = self.timeout_tries - 1
                    print("reCAPTCHA detected. Waiting for user to solve...")
                    winsound.Beep(1000, 500)
                    start_time = time.time()
                    while time.time() - start_time < 40:
                        try:
                            if 'google.com/sorry' not in self.driver.current_url:
                                print("reCAPTCHA solved. Continuing...")
                                time.sleep(3)
                                break
                        except Exception as e:
                            pass
                        time.sleep(1)  # Check every second
                    try:
                        if 'google.com/sorry' in self.driver.current_url:
                            print("reCAPTCHA not solved within returning 'failed'.")
                            google_enabled = False
                            if self.load_page_and_check(bing_url):
                                bing_enabled = True
                            else:
                                bing_enabled = False
                    except Exception as e:
                        pass
            except Exception as e:
                pass
            if not google_enabled and not bing_enabled:
                print("No links Found!")
                fault += 1
                continue
            # else:
            #     time.sleep(1)
            a_tag_list = []
            descriptions_list = []
            engine = ''
            if google_enabled:
                engine = 'google.com'
            if bing_enabled:
                engine = 'bing.com'
            try:
                a_tag_list = self.driver.find_elements(By.XPATH, self.get_engine_XPATH_by(engine))
                if len(a_tag_list)==0:
                    raise Exception("error: wrong xpath!")
            except Exception as e:
                try:
                    a_tag_list = self.driver.find_elements(By.XPATH, self.get_alternative_engine_XPATH_by(engine))
                except Exception as e:
                    print("No links Found!")
                    fault += 1
                    continue
            try:
                descriptions_list = self.driver.find_elements(By.CSS_SELECTOR, self.get_class_description_by(engine))
                if len(descriptions_list)==0:
                    raise Exception("error: wrong css selector!")
            except Exception as e:
                try:
                    descriptions_list = self.driver.find_elements(By.CSS_SELECTOR, self.get_alternative_class_description_by(engine))
                except Exception as e:
                    print("No descriptions Found!")
                    fault += 1
                    continue
            if len(a_tag_list)==0 or len(descriptions_list)==0:
                print(f"error, len(title): {len(a_tag_list)}, len(description): {len(descriptions_list)}")
                continue
            a_tag, description = "", ""
            for a in a_tag_list:
                a_tag = a_tag + a.text + "\n\n"
            for d in descriptions_list:
                description = description + d.text + "\n\n"
            ai_text = ""
            try:
                ai_text = WebDriverWait(self.driver, 1).until(EC.presence_of_element_located((By.XPATH, "//div[@class='mZJni Dn7Fzd']"))).text
                if not ai_text:
                    raise Exception()
            except Exception:
                try:
                    ai_text = WebDriverWait(self.driver, 1).until(EC.presence_of_element_located((By.XPATH, "//div[@class='wDYxhc']"))).text
                    if not ai_text:
                        raise Exception()
                except Exception:
                    try:
                        ai_text = WebDriverWait(self.driver, 1).until(EC.presence_of_element_located((By.XPATH, "//div[@class='V3FYCf']"))).text
                    except Exception:
                        pass
            ai_text = self.remove_single_char_number_lines(ai_text) if ai_text else ""
            if ai_text.strip():
                a_tag = "Google's summary answer:\n\n" + a_tag
                description = ai_text + "\n\n" + description
            if self.update_title_description(id, a_tag, description, state):
                fault = 0
            else:
                print("No title or description is found to update table!")
        self.driver.quit()


    def update_state_zero_one(self, audit_id: int, category: str, brand: str, model: str) -> bool:
        if category and brand and model:
            if category.strip() and brand.strip() and model.strip():
                if not self.has_no_persian(category+brand+model):
                    print("Error While updating state zero one: cat/br/m has persian char!")
                    return False
            else:
                print("Error While updating state zero one: cat/br/m is empty!")
                return False
        else:
            print("Error While updating state zero one: cat/br/m is empty!")
            return False
        model = model.replace('- ','-').replace(' -','-').replace('/ ','/').replace(' /','/').replace(' (','(').replace(') ',')').upper().strip()
        model = model.replace('- ','-').replace(' -','-').replace('/ ','/').replace(' /','/').replace(' (','(').replace(') ',')').replace(' ','-').replace('_','-')
        brand = brand.upper().strip()
        m_truncated = self.get_truncated_model(model)
        m_id = self.find_similar_model_id(category, brand, m_truncated)
        conn = None
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            if m_id == -1:
                cursor.execute("INSERT INTO Models (category, brand, model, truncated_model) VALUES (?, ?, ?, ?)",
                    (category, brand, model, m_truncated))
                conn.commit()
                m_id = cursor.lastrowid
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            cursor.execute("UPDATE Audits SET model_id = ?, category = ? , brand = ? , model = ? , updated_date = ? , state = 2 WHERE id = ?",(m_id, category, brand, model, current_date, audit_id))
            conn.commit()
            print(f"id: {audit_id} state-zero-one has been updated successfully.")
            self.autocomplete_by_history(audit_id, brand+'-'+model, model, brand, model, 2)
            return True
        except Exception as e:
            print("Error has occurred in update_state_zero_one: ", e)
            return False
        finally:
            if conn:
                conn.close()


    @staticmethod
    def has_text(text: str):
        if text == None:
            return False
        if text.strip():
            return True
        else:
            return False


    @staticmethod
    def split_groups(text: str):
        """Split text into groups of consecutive non-empty lines."""
        raw_groups = re.split(r"\n\s*\n+", text.strip())
        groups = []
        for g in raw_groups:
            lines = [line for line in g.splitlines() if line.strip() != ""]
            if lines:
                groups.append("\n".join(lines))
        return groups


    def mix_texts(self, text1: str, text2: str) -> str:
        """Mix two texts according to the required format."""
        groups1 = self.split_groups(text1)
        groups2 = self.split_groups(text2)
        mixed_parts = []
        max_len = max(len(groups1), len(groups2))
        for i in range(max_len):
            part = ""
            if i < len(groups1):
                part += groups1[i] + ":"
            if i < len(groups2):
                part += "\n" + groups2[i]
            mixed_parts.append(part)
        return "\n\n".join(mixed_parts)

    def call_for_gpt_4o_mini(self, system_prompt: str, user_prompt: str, tool_schema: json):
        client = OpenAI(
        api_key = open('openai_api.txt', 'r',encoding='utf-8').read()
        )
        payment_model = "gpt-4o-mini"
        # print(f"Calling model: {payment_model}")
        temperature = 0 if payment_model != "gpt-5-mini" else 1
        completion = client.chat.completions.create(
        model=payment_model,
        store=True,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        tools=[tool_schema],
        tool_choice={"type": "function", "function": {"name": tool_schema["function"]["name"]}},
        temperature = temperature
        )
        print(f"prompt tokens: {completion.usage.prompt_tokens} result tokens: {completion.usage.completion_tokens} total tokens: {completion.usage.total_tokens}")
        # return completion.choices[0].message.tool_calls[0].function.arguments
        resp_msg = completion.choices[0].message
        if resp_msg.tool_calls:
            return resp_msg.tool_calls[0].function.arguments
        else:
            # Fallback: maybe the model returned plain JSON
            return resp_msg.content  # or json.loads(resp_msg.content)

    def update_models_brand(self, wrong_brand: str, correct_brand: str):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("SELECT id, truncated_model FROM Models WHERE brand = ?", (wrong_brand,))
            rows = cursor.fetchall()
            conn.close()
            for model_id, truncated_model in rows:
                try:
                    conn = sqlite3.connect(self.db_name, timeout=30)
                    cursor = conn.cursor()
                    cursor.execute("UPDATE Models SET brand = ? WHERE id = ?", (correct_brand, model_id))
                    conn.commit()
                except sqlite3.IntegrityError:
                    cursor.execute("SELECT id, brand, category FROM Models WHERE brand = ? AND truncated_model = ?", (correct_brand, truncated_model))
                    existing_model = cursor.fetchone()
                    if existing_model:
                        new_model_id, new_brand, new_category = existing_model
                        cursor.execute("UPDATE Audits SET model_id = ? , uncertain_brand = ? , brand = ? , uncertain_category = ? , category = ? WHERE model_id = ?", (new_model_id, new_brand, new_brand, new_category, new_category, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Features WHERE model_id = ?", (new_model_id,))
                        cursor.execute("UPDATE Features SET model_id = ? WHERE model_id = ?", (new_model_id, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Models WHERE id = ?", (model_id,))
                        conn.commit()
                        print(f"Model id {model_id} merged into {new_model_id}")
        except Exception as e:
            print(f"Error in update_models_brand: {e}")
        finally:
            if conn:
                conn.close()

    def remove_bmf_tmf_sbs_suffix_from_refrigerators(self):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.id, m.model, m.brand
                FROM Models m
                WHERE m.category = 'Refrigerator' AND 
                (m.model LIKE '%-BMF' OR m.model LIKE '%BMF' OR 
                 m.model LIKE '%-TMF' OR m.model LIKE '%TMF' OR 
                 m.model LIKE '%-SBS' OR m.model LIKE '%SBS')
            """)
            rows = cursor.fetchall()
            for model_id, model, brand in rows:
                new_model = model
                if new_model.upper().endswith('-BMF'):
                    new_model = new_model.upper().removesuffix('-BMF')
                if new_model.upper().endswith('BMF'):
                    new_model = new_model.upper().removesuffix('BMF')
                if new_model.upper().endswith('-TMF'):
                    new_model = new_model.upper().removesuffix('-TMF')
                if new_model.upper().endswith('TMF'):
                    new_model = new_model.upper().removesuffix('TMF')
                if new_model.upper().endswith('-SBS'):
                    new_model = new_model.upper().removesuffix('-SBS')
                if new_model.upper().endswith('SBS'):
                    new_model = new_model.upper().removesuffix('SBS')
                new_truncated = self.get_truncated_model(new_model)
                try:
                    cursor.execute("UPDATE Models SET model = ?, truncated_model = ? WHERE id = ?",
                        (new_model, new_truncated, model_id))
                    conn.commit()
                    print(f"Model id {model_id} updated: {model} → {new_model}")
                except sqlite3.IntegrityError:
                    cursor.execute("SELECT id FROM Models WHERE brand = ? AND truncated_model = ? LIMIT 1", (brand, new_truncated))
                    existing = cursor.fetchone()
                    if existing:
                        new_model_id = existing[0]
                        cursor.execute("UPDATE Audits SET model_id = ?, model = ? WHERE model_id = ?", (new_model_id, new_model, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Features WHERE model_id = ?", (new_model_id,))
                        cursor.execute("UPDATE Features SET model_id = ? WHERE model_id = ?", (new_model_id, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Models WHERE id = ?", (model_id,))
                        conn.commit()
                        print(f"Model id {model_id} merged into {new_model_id}, Audits and Features updated")
        except Exception as e:
            print(f"Error in remove_bmf_tmf_sbs_suffix_from_refrigerators: {e}")
        finally:
            if conn:
                conn.close()

    def distinguish_Ref_Frz_models(self):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT m.id, m.model, m.brand, f.value AS type
                FROM Models m
                JOIN Features f ON f.model_id = m.id AND f.name = 'type'
                WHERE m.category = 'Refrigerator' AND f.value IN ('SD-Frz', 'SD-Ref', 'TWIN')
            """)
            rows = cursor.fetchall()
            for model_id, model, brand, type_value in rows:
                if type_value == 'SD-Frz':
                    suffix = '-FRZ'
                    check_suffixes = ('-FRZ', '-Frz', 'FRZ')
                elif type_value == 'SD-Ref':
                    suffix = '-REF'
                    check_suffixes = ('-REF', '-Ref', 'REF')
                elif type_value == 'TWIN':
                    suffix = '-TWIN'
                    check_suffixes = ('-TWIN', '-Twin', 'TWIN')
                else:
                    continue
                if any(model.upper().endswith(s.upper()) for s in check_suffixes):
                    continue
                new_model = model + suffix
                new_truncated = self.get_truncated_model(new_model)
                try:
                    cursor.execute("UPDATE Models SET model = ?, truncated_model = ? WHERE id = ?",
                        (new_model, new_truncated, model_id))
                    conn.commit()
                    print(f"Model id {model_id} updated: {model} → {new_model}")
                except sqlite3.IntegrityError:
                    cursor.execute("SELECT id FROM Models WHERE brand = ? AND truncated_model = ? LIMIT 1", (brand, new_truncated))
                    existing = cursor.fetchone()
                    if existing:
                        new_model_id = existing[0]
                        cursor.execute("UPDATE Audits SET model_id = ?, model = ? WHERE model_id = ?", (new_model_id, new_model, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Features WHERE model_id = ?", (new_model_id,))
                        cursor.execute("UPDATE Features SET model_id = ? WHERE model_id = ?", (new_model_id, model_id))
                        conn.commit()
                        cursor.execute("DELETE FROM Models WHERE id = ?", (model_id,))
                        conn.commit()
                        print(f"Model id {model_id} merged into {new_model_id}, Audits and Features updated")
        except Exception as e:
            print(f"Error in distinguish_Ref_Frz_models: {e}")
        finally:
            if conn:
                conn.close()


    def category_brand_unifying(self):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("UPDATE Audits SET category = 'Air Conditioner' WHERE category = 'Air Cooling';")
            count = cursor.rowcount
            if count:
                print(f"category: Air Cooling -> Air Conditioner in {count} rows has corrected.")
            conn.commit()
        except Exception as e:
            print("error has occured in method category_brand_unifying: ", e)
        finally:
            if conn:
                conn.close()
        df = pd.read_excel(self.mapping_path, sheet_name='correct_brand')
        queries = ["""UPDATE Models SET brand = ? WHERE brand = ?;""",
                 """UPDATE Audits SET brand = ? WHERE brand = ?;""",
                 """UPDATE Audits SET uncertain_brand = ? WHERE uncertain_brand = ?;"""]
        for _, row in df.iterrows():
            parameters = (row['correct'],row['wrong'])
            for index, query in enumerate(queries):
                conn = None
                try:
                    conn = sqlite3.connect(self.db_name, timeout=30)
                    cursor = conn.cursor()
                    cursor.execute(query, parameters)
                    conn.commit()
                    count = cursor.rowcount
                    if count:
                        print(f"brand: {row['correct']} in {count} rows has corrected.")
                except sqlite3.IntegrityError as e:
                    if index==0:
                        if conn:
                            conn.close()
                        self.update_models_brand(row['wrong'], row['correct'])
                except Exception as e:
                    print(f"error occured in method brand_unifying() for values:{parameters} : ", e)
                finally:
                    if conn:
                        conn.close()

    def state_zero_one_to_two(self):
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, model_id, truncated_text, uncertain_brand, uncertain_category, uncertain_type, search_titles FROM Audits
            WHERE state<2 AND search_titles IS NOT NULL AND search_titles != '' """)
        rows = cursor.fetchall()
        conn.close()
        print(f"Number Of Models: {len(rows)}")
        if len(rows) == 0:
            print("There is no more row to process!")
        else:
            system_prompt = open('SmartDataExtractor/prompts/title_prompt.txt', 'r',encoding='utf-8').read()
            tool_schema = None
            with open("SmartDataExtractor/prompts/state01.json", "r", encoding="utf-8") as f:
                tool_schema = json.load(f)
            all_count = len(rows)
            this_count = 0
            fault = 0
            for row in rows:
                this_count += 1
                print(f"Proccessing row {this_count}/{all_count}:")
                if fault >= 5:
                    print("Automation failed due to 5 unsuccessful attempts! end.")
                    return
                id, model_id, truncated_text, uncertain_brand, uncertain_category, uncertain_type, search_titles = row
                persian_cat = self.get_persian_category(uncertain_category) if uncertain_category else ""
                persian_brand = self.get_persian_brand(uncertain_brand) if uncertain_brand else ""
                search_engine_prompt = persian_cat + " " + persian_brand + " " + (uncertain_brand if uncertain_brand else "") + " " + truncated_text
                print(f"search engine prompt: {search_engine_prompt}")
                user_prompt = f"""
                The text for Google search:
                {search_engine_prompt}

                The Google Engine result titles:
                {search_titles}

                Please output ONLY the following JSON:
                {{
                "category": "***",
                "brand": "***",
                "model": "***"
                }}
                """
                try:
                    ai_result = self.call_for_gpt_4o_mini(system_prompt, user_prompt, tool_schema)
                    parsed_data = json.loads(ai_result)
                    # category = uncertain_category if uncertain_category else parsed_data.get("category","")
                    # brand = uncertain_brand if uncertain_brand else parsed_data.get("brand","")
                    category = parsed_data.get("category","")
                    brand = parsed_data.get("brand","")
                    model = parsed_data.get("model","")
                    update_result = self.update_state_zero_one(id, category, brand, model)
                    if update_result:
                        fault = 0
                    else:
                        raise Exception("Updaing state zero-one exception!")
                except Exception as e:
                    print("Error has occured: ", e)
                    fault += 1
        self.category_brand_unifying()
        print("end.")

    def has_value_for_feature(self, model_id: int, feature_name: str) -> bool:
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM Features WHERE model_id = ? AND name = ? AND value IS NOT NULL AND value != '' LIMIT 1",
                (model_id, feature_name)
            )
            row = cursor.fetchone()
            return bool(row)
        except Exception as e:
            print("Error in has_value_for_feature:", e)
            return False
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass   

    def update_state_two(self, model_id, capacity, type, technology, smart):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            if capacity and not self.has_value_for_feature(model_id, "capacity"):
                cursor.execute("INSERT INTO Features (audit_id, model_id, name, value) VALUES ((SELECT id FROM Audits WHERE model_id = ? LIMIT 1), ?, 'capacity', ?)",(model_id, model_id, capacity))
                conn.commit()
            if type and not self.has_value_for_feature(model_id, "type"):
                cursor.execute("INSERT INTO Features (audit_id, model_id, name, value) VALUES ((SELECT id FROM Audits WHERE model_id = ? LIMIT 1), ?, 'type', ?)",(model_id, model_id, type))
                conn.commit()
            if technology and not self.has_value_for_feature(model_id, "technology"):
                cursor.execute("INSERT INTO Features (audit_id, model_id, name, value) VALUES ((SELECT id FROM Audits WHERE model_id = ? LIMIT 1), ?, 'technology', ?)",(model_id, model_id, technology))
                conn.commit()
            if smart and not self.has_value_for_feature(model_id, "smart"):
                cursor.execute("INSERT INTO Features (audit_id, model_id, name, value) VALUES ((SELECT id FROM Audits WHERE model_id = ? LIMIT 1), ?, 'smart', ?)",(model_id, model_id, smart))
                conn.commit()
            if not (capacity or type or technology or smart):
                print("No feature to update in update_state_two!")
                return False
            cursor.execute("UPDATE Audits SET updated_date = ? , state = 3 WHERE model_id = ?",(current_date, model_id))
            conn.commit()
            print(f"model id: {model_id} state-two has been updated successfully.")
            return True
        except Exception as e:
            print("Error has occurred in update_state_two: ", e)
            return False
        finally:
            if conn:
                conn.close()

    def get_persian_type_by_category(self, category: str, type: str) -> str:
        if category=='Refrigerator':
            match type:
                case 'SD-Ref':
                    return 'یخچال تک'
                case 'SD-Frz':
                    return 'فریزر تک'
                case 'SBS':
                    return 'یخچال فریزر ساید بای ساید'
                case 'TMF':
                    return 'یخچال کمبی فریزر بالا'
                case 'BMF':
                    return 'یخچال کمبی فریزر پایین'
                case 'TWIN':
                    return 'یخچال فریزر دوقلو'
                case 'TMF/BMF':
                    return 'یخچال فریزر کمبی'
                case 'Chest-Frz':
                    return 'فریزر صندوقی'
                case 'SD-Mini Bar':
                    return 'یخچال مینی بار'
                case _:
                    return 'یخچال فریزر'
        else:
            return self.get_persian_category(category) if category else ""

    def check_for_delete_wrong_features(self):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM Features WHERE value IN ('null', '') OR (name = 'smart' AND value = 'Inverter') OR (name = 'technology' and value IN ('SMART',34,'Auto'));")
            conn.commit()
            count = cursor.rowcount
            if count:
                print(f"{count} wrong features is deleted.")
        except Exception as e:
            print("error has occured in method category_brand_unifying: ", e)
        finally:
            if conn:
                conn.close()
        wrong_capacity = """DELETE FROM Features
            WHERE name = 'capacity'
            AND CAST(value AS INTEGER) >= 1000
            AND model_id IN (
                    SELECT id
                    FROM Models
                    WHERE category = 'Refrigerator'
            );
            """
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute(wrong_capacity)
        conn.commit()
        deleted_rows = cursor.rowcount
        conn.close()
        print(f"{deleted_rows} rows of refrigerators had wrong capacity and were deleted!")
        wrong_capacity = """DELETE FROM Features
            WHERE name = 'capacity'
            AND (value='/' OR value='//' OR value='///' OR value='////')
            AND model_id IN (
                    SELECT id
                    FROM Models
                    WHERE category = 'Vacuum Cleaner'
            );
            """
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute(wrong_capacity)
        conn.commit()
        deleted_rows = cursor.rowcount
        conn.close()
        print(f"{deleted_rows} rows of Vacuum Cleaner had wrong capacity and were deleted!")

    def state_two_to_three(self, state = 2, feature=""):
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        query = """SELECT m.id, m.category, m.brand, m.model, a.truncated_text, a.uncertain_type, a.search_titles AS titles, a.search_descriptions AS description FROM Models m
            JOIN Audits a ON a.model_id = m.id AND a.search_descriptions IS NOT NULL AND a.search_descriptions != ''
            LEFT JOIN Features f ON f.model_id = m.id
            GROUP BY m.id
            HAVING COUNT(DISTINCT f.name) < 4;"""
        if state == 3:
            query = """SELECT m.id, m.category, m.brand, m.model, a.truncated_text, a.uncertain_type, a.search_titles AS titles, a.search_descriptions AS description FROM Models m
                JOIN Audits a ON a.model_id = m.id AND a.search_descriptions IS NOT NULL AND a.search_descriptions != ''
                LEFT JOIN Features f ON f.model_id = m.id
                GROUP BY m.id
                HAVING SUM(CASE WHEN f.name = '"""+feature+"""' THEN 1 ELSE 0 END) = 0;"""
        cursor.execute(query)
        # cursor.execute("""SELECT m.id, m.category, m.brand, m.model, a.truncated_text, a.uncertain_type, a.search_titles AS titles, a.search_descriptions AS description
        # FROM Models m JOIN Audits a ON a.model_id = m.id
        # WHERE a.search_descriptions IS NOT NULL AND a.search_descriptions != ''
        # AND (
        # NOT EXISTS (SELECT 1 FROM Features f WHERE f.model_id = m.id AND f.name = 'capacity' AND f.value IS NOT NULL AND f.value != '')
        # OR NOT EXISTS (SELECT 1 FROM Features f WHERE f.model_id = m.id AND f.name = 'type' AND f.value IS NOT NULL AND f.value != '')
        # OR NOT EXISTS (SELECT 1 FROM Features f WHERE f.model_id = m.id AND f.name = 'technology' AND f.value IS NOT NULL AND f.value != '')
        # OR NOT EXISTS (SELECT 1 FROM Features f WHERE f.model_id = m.id AND f.name = 'smart' AND f.value IS NOT NULL AND f.value != '')
        # )
        # GROUP BY m.id;""")
        rows = cursor.fetchall()
        conn.close()
        print(f"Number Of Models: {len(rows)}")
        if len(rows) == 0:
            print("There is no more row to process! end.")
            return
        tool_schema = None
        all_count = len(rows)
        this_count = 0
        fault = 0
        for row in rows:
            this_count += 1
            print(f"Proccessing row {this_count}/{all_count}:")
            if fault >= 5:
                print("Automation failed due to 5 unsuccessful attempts! end.")
                return
            model_id, category, brand, model, truncated_text, uncertain_type, search_titles, search_description = row
            persian_brand = self.get_persian_brand(brand) if brand else ""
            search_engine_prompt = self.get_persian_type_by_category(category, uncertain_type) + " " + persian_brand + ((" "+brand) if (brand and (brand.upper().strip() != persian_brand)) else "") + ((" "+truncated_text) if not self.has_no_persian(truncated_text) else (" مدل " + model))
            print(f"search engine prompt: {search_engine_prompt}") # search engine prompt ****************************
            system_prompt = open('SmartDataExtractor/prompts/'+ category +'_prompt.txt', 'r',encoding='utf-8').read()
            mixed_text = self.mix_texts(search_titles, search_description)
            user_prompt = f"""
            The text for Google search:
            {search_engine_prompt}

            The Google Engine result description:
            
            {mixed_text}
            """
            with open("SmartDataExtractor/prompts/"+ category +".json", "r", encoding="utf-8") as f:
                tool_schema = json.load(f)
            try:
                ai_result = self.call_for_gpt_4o_mini(system_prompt, user_prompt, tool_schema)
                parsed_data = json.loads(ai_result)
                capacity = parsed_data.get("capacity",None)
                type = uncertain_type if uncertain_type else parsed_data.get("type",None)
                # type = parsed_data.get("type",None)
                if uncertain_type == 'TMF/BMF' and parsed_data.get("type",None) != 'TMF/BMF' and parsed_data.get("type",None) != None:
                    type = parsed_data.get("type",None)
                technology = parsed_data.get("technology",None)
                smart = parsed_data.get("smart",None)
                update_result = self.update_state_two(model_id, capacity, type, technology, smart)
                if update_result:
                    fault = 0
                else:
                    raise Exception("Updaing state_two_to_three exception!")
            except Exception as e:
                print("Error has occured: ", e)
                fault += 1
        self.check_for_delete_wrong_features()

    def clean_dataset(self, dataset: pd.DataFrame) -> pd.DataFrame:
        pass # code here
        return dataset

    @staticmethod
    def get_feature_value(cursor, model_id, audit_id, feature_name):
        cursor.execute(
            "SELECT value FROM Features WHERE "+ ("model_id = ?" if model_id else "audit_id = ?")+" AND name=?",
            (model_id if model_id else audit_id, feature_name)
        )
        row = cursor.fetchone()
        return row[0] if row else ""
    
    def export_data_for(self, EXCEL_NAME, SHEET_NAME):
        self.category_brand_unifying()
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        output_rows = []
        df = None
        if EXCEL_NAME and SHEET_NAME:
            df = pd.read_excel("SmartDataExtractor/"+EXCEL_NAME, sheet_name=SHEET_NAME)
            if "کالا" not in df.columns:
                raise ValueError("Excel file must contain column 'کالا'")
        elif not EXCEL_NAME and not SHEET_NAME:
            cursor.execute("SELECT full_text FROM Audits")
            rows = cursor.fetchall()
            df = pd.DataFrame(rows, columns=["کالا"])
        for idx, row in df.iterrows():
            kala = str(row["کالا"])
            cursor.execute(
                "SELECT id, model_id FROM Audits WHERE full_text=?",
                (kala,)
            )
            audit = cursor.fetchone()
            if not audit:
                output_rows.append({
                    "کالا": kala,
                    "کد مدل": "",
                    "محصول": "",
                    "برند": "",
                    "ظرفیت": "",
                    "نوع": "",
                    "تکنولوژی": "",
                    "SMART/NON": "",
                })
                continue
            audit_id, model_id = audit
            new_row = None
            unified_model = model_name = category = brand = ""
            if model_id:
                cursor.execute(
                    "SELECT category, brand, model, unified_model FROM Models WHERE id=?",
                    (model_id,)
                )
                model_row = cursor.fetchone()
                if model_row:
                    category, brand, model_name, unified_model = model_row
                if unified_model:
                    tr_unified = self.get_truncated_model(unified_model)
                    id_unified = self.find_similar_model_id(category, brand, tr_unified)
                    if id_unified != -1:
                        model_id = id_unified
            capacity_value = self.get_feature_value(cursor, model_id, audit_id, "capacity")
            type_value = self.get_feature_value(cursor, model_id, audit_id, "type")
            if not type_value:
                cursor.execute("SELECT uncertain_type FROM Audits WHERE id=?", (audit_id,))
                row = cursor.fetchone()
                if row:
                    type_value = row[0]
            technology_value = self.get_feature_value(cursor, model_id, audit_id, "technology")
            smart_value = self.get_feature_value(cursor, model_id, audit_id, "smart")
            if not model_id:
                cursor.execute(
                "SELECT uncertain_category, uncertain_brand FROM Audits WHERE id=?",
                (audit_id,)
                )
                audit_row = cursor.fetchone()
                category_ = brand_ = ""
                if audit_row:
                    category_, brand_ = audit_row
                new_row = {
                    "کالا": kala,
                    "کد مدل": "",
                    "محصول": category_,
                    "برند": brand_,
                    "ظرفیت": capacity_value,
                    "نوع": type_value,
                    "تکنولوژی": technology_value,
                    "SMART/NON": smart_value,
                }
            else:
                new_row = {
                    "کالا": kala,
                    "کد مدل": unified_model if unified_model else model_name,
                    "محصول": category,
                    "برند": brand,
                    "ظرفیت": capacity_value,
                    "نوع": type_value,
                    "تکنولوژی": technology_value,
                    "SMART/NON": smart_value,
                }
            output_rows.append(new_row)
        conn.close()
        out_df = pd.DataFrame(output_rows)
        out_df = self.clean_dataset(out_df) # clean dataset
        out_df.to_excel("SmartDataExtractor/output.xlsx", index=False)
        print("✔ Output Excel created: SmartDataExtractor/output.xlsx")

    def get_empty_features(self, model_id: int) -> list[str]:
        required_features = ["capacity", "type", "technology", "smart"]
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        empty_features = []
        for feature_name in required_features:
            cursor.execute("""
                SELECT value
                FROM Features
                WHERE model_id = ? AND name = ?
            """, (model_id, feature_name))
            row = cursor.fetchone()
            if row is None or row[0] is None or row[0].strip() == "":
                empty_features.append(feature_name)
        conn.close()
        return empty_features

    def get_user_feature_value(self, category: str, feature: str, model: str) -> str | None:
        """
        Opens a Tkinter form asking the user to enter/select a feature value.

        Returns:
            str: selected radio value or typed text
            None: if SKIP is pressed
        """
        json_path = os.path.join("SmartDataExtractor", "prompts", f"{category}.json")
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"JSON file not found: {json_path}")
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            enum_values = data["function"]["parameters"]["properties"][feature]["enum"]
        except KeyError:
            enum_values = []  # feature has no enum section → no radio buttons
        try:
            description = data["function"]["parameters"]["properties"][feature]["description"]
        except KeyError:
            description = ""
        result = {"value": None}
        settings = self.load_settings()
        def on_ok():
            typed = entry.get().strip()
            selected_radio = radio_var.get().strip()
            if not typed and not selected_radio:
                warning_label.config(
                    text="text box is empty or no option selected!", foreground="red"
                )
                return
            if selected_radio:
                result["value"] = selected_radio
            else:
                result["value"] = typed
            settings["feature_menu_x"] = window.winfo_x()
            settings["feature_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        def on_skip():
            result["value"] = None
            settings["feature_menu_x"] = window.winfo_x()
            settings["feature_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        window = tk.Toplevel()
        window.title(f"Enter the {feature} for {model}")
        x = settings.get("feature_menu_x", 500)
        y = settings.get("feature_menu_y", 100)
        window.geometry(f"400x500+{x}+{y}")
        window.resizable(False, False)
        window.bind("<Return>", lambda event: on_ok())
        window.bind("<Escape>", lambda event: on_skip())
        window.grab_set() # Make modal
        window.focus_set()
        ttk.Label(
            window,
            text=f"Enter the {feature} for {model}",
            font=("Arial", 12)
        ).pack(pady=10)
        frame = ttk.Frame(window)
        frame.pack(pady=5)
        ttk.Label(frame, text=f"{feature}: ").grid(row=0, column=0, padx=5)
        entry = ttk.Entry(frame, width=30)
        entry.grid(row=0, column=1)
        entry.focus()
        ttk.Label(window, text=description+"\n\nSelect one option (optional):").pack(pady=10)
        radio_var = tk.StringVar(value="")
        radio_frame = ttk.Frame(window)
        radio_frame.pack()
        for value in enum_values:
            ttk.Radiobutton(
                radio_frame,
                text=value,
                value=value,
                variable=radio_var
            ).pack(anchor="w")
        warning_label = ttk.Label(window, text="", font=("Arial", 10))
        warning_label.pack(pady=5)
        btn_frame = ttk.Frame(window)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="OK", width=12, command=on_ok).grid(row=0, column=0, padx=10)
        ttk.Button(btn_frame, text="SKIP", width=12, command=on_skip).grid(row=0, column=1, padx=10)
        window.wait_window()
        return result["value"]

    def get_exclusive_feature_definition_by(self, feature: str, category: str) -> str:
        df = pd.read_excel(self.mapping_path, sheet_name="features_definition")
        for _, row in df.iterrows():
            if row['feature']==feature and row['category']==category:
                return row['definition']
        return ""

    def get_user_input(self, feature: str, model: str) -> str | None:
        result = {"value": None}
        def on_ok():
            text = entry.get().strip()
            if not text:
                warning_label.config(text="text box is empty! fill it!", foreground="red")
            else:
                result["value"] = text
                window.destroy()
        def on_skip():
            result["value"] = None
            window.destroy()
        window = tk.Tk()
        window.title(f"Enter the {feature} for {model}")
        window.geometry("350x200")
        window.resizable(False, False)
        ttk.Label(window, text=f"Enter the {feature} for {model}", font=("Arial", 12)).pack(pady=10)
        frame = ttk.Frame(window)
        frame.pack(pady=5)
        ttk.Label(frame, text=f"{feature}: ").grid(row=0, column=0, padx=5)
        entry = ttk.Entry(frame, width=25)
        entry.grid(row=0, column=1)
        warning_label = ttk.Label(window, text="", font=("Arial", 10))
        warning_label.pack(pady=5)
        btn_frame = ttk.Frame(window)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="OK", width=10, command=on_ok).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="SKIP", width=10, command=on_skip).grid(row=0, column=1, padx=5)
        entry.focus()
        window.mainloop()
        return result["value"]

    def complementary_search(self):
        features = ['capacity', 'type', 'technology', 'smart']
        for feature in features:
            self.extract_engine_search(3, feature)
            self.state_two_to_three(3, feature)
        print("end.")

    def manual_search_feature(self):
        conn = sqlite3.connect(self.db_name, timeout=30)
        conn.row_factory = sqlite3.Row  # Enable row factory for better access
        cursor = conn.cursor()
        query = """SELECT m.id, m.category, m.brand, m.model, a.truncated_text, a.uncertain_type, a.search_titles AS titles, a.search_descriptions AS description FROM Models m
            JOIN Audits a ON a.model_id = m.id AND a.search_descriptions IS NOT NULL AND a.search_descriptions != ''
            LEFT JOIN Features f ON f.model_id = m.id
            GROUP BY m.id
            HAVING COUNT(DISTINCT f.name) >= 1 AND COUNT(DISTINCT f.name) < 4;"""
        cursor.execute(query)
        rows = cursor.fetchall()
        if conn:
            conn.close()  # Ensure connection is closed properly
        all = len(rows)
        if all == 0:
            print("There is no more row to process! end.")
            return
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-extensions")
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        self.driver.set_page_load_timeout(8)
        time.sleep(1)
        fault = 0
        count = 0
        for row in rows:
            if fault > 5:
                print("Automation failed due to 5 unsuccessful attempts!")
                return
            count += 1
            model_id, category, brand, model, truncated_text, uncertain_type, search_titles, search_description = row
            print(f"row number: {count}/{all}   ID: {model_id}")
            empty_features = self.get_empty_features(model_id)
            persian_brand = self.get_persian_brand(brand) if brand else ""
            search_engine_prompt = self.get_persian_type_by_category(category, uncertain_type) + " " + persian_brand + ((" "+brand) if (brand and (brand.upper().strip() != persian_brand)) else "") + ((" "+truncated_text) if not self.has_no_persian(truncated_text) else (" مدل " + model))
            included = False
            capacity = type = technology = smart = None
            for feature in empty_features:
                match feature:
                    case "capacity":
                        feature_definition = (self.get_exclusive_feature_definition_by("capacity", category)+" ") if "capacity" in empty_features else ""
                        search_engine_prompt = feature_definition + search_engine_prompt
                        google_url = 'https://www.google.com/search?q=' + search_engine_prompt.strip()
                        if not self.load_page_and_check(google_url):
                            print(f"failed while loading search engine prompt: {search_engine_prompt}")
                            fault += 1
                            continue
                        else:
                            capacity = self.get_user_feature_value(category, feature, brand+" "+model)
                    case "type":
                        feature_definition = (self.get_exclusive_feature_definition_by("type", category)+" ") if "type" in empty_features else ""
                        search_engine_prompt = feature_definition + search_engine_prompt
                        google_url = 'https://www.google.com/search?q=' + search_engine_prompt.strip()
                        if not self.load_page_and_check(google_url):
                            print(f"failed while loading search engine prompt: {search_engine_prompt}")
                            fault += 1
                            continue
                        else:
                            type = self.get_user_feature_value(category, feature, brand+" "+model)
                    case "technology":
                        feature_definition = (self.get_exclusive_feature_definition_by("technology", category)+" ") if "technology" in empty_features else ""
                        search_engine_prompt = feature_definition + search_engine_prompt
                        google_url = 'https://www.google.com/search?q=' + search_engine_prompt.strip()
                        if not self.load_page_and_check(google_url):
                            print(f"failed while loading search engine prompt: {search_engine_prompt}")
                            fault += 1
                            continue
                        else:
                            technology = self.get_user_feature_value(category, feature, brand+" "+model)
                    case "smart":
                        feature_definition = (self.get_exclusive_feature_definition_by("smart", category)+" ") if "smart" in empty_features else ""
                        search_engine_prompt = feature_definition + search_engine_prompt
                        google_url = 'https://www.google.com/search?q=' + search_engine_prompt.strip()
                        if not self.load_page_and_check(google_url):
                            print(f"failed while loading search engine prompt: {search_engine_prompt}")
                            fault += 1
                            continue
                        else:
                            smart = self.get_user_feature_value(category, feature, brand+" "+model)
            for feature in empty_features:
                if (feature=='capacity' and capacity) or (feature=='type' and type) or (feature=='technology' and technology) or (feature=='smart' and smart):
                    included = True
            if included:
                update_result = self.update_state_two(model_id, capacity, type, technology, smart)
                if update_result:
                    fault = 0
        print("end.")

    def get_info_from_user_for_model(self, full_text: str, model: str, u_model:str, category: str, brand: str, type: str, u_category: str, u_brand: str, u_type: str) -> tuple[str, str, str, str]:
        result = {"category": "", "brand": "", "type": "", "model": ""}
        settings = self.load_settings()
        def on_ok():
            # Collect values with priority: textbox > radio button
            result["category"] = entry_category.get().strip() or radio_var_category.get().strip() or ""
            result["brand"] = entry_brand.get().strip() or radio_var_brand.get().strip() or ""
            result["type"] = entry_type.get().strip() or radio_var_type.get().strip() or ""
            result["model"] = entry_model.get().strip() or radio_var_model.get().strip() or ""
            if (not result["category"]) or (not result["brand"]) or (not result["type"]) or (not result["model"]):
                warning_label.config(text="All fields must be filled!", foreground="red")
                return
            settings["model_menu_x"]  = window.winfo_x()
            settings["model_menu_y"]  = window.winfo_y()
            self.save_settings(settings)
            window.destroy()

        def on_skip():
            result["category"] = ""
            result["brand"] = ""
            result["type"] = ""
            result["model"] = ""
            settings["model_menu_x"]  = window.winfo_x()
            settings["model_menu_y"]  = window.winfo_y()
            self.save_settings(settings)
            window.destroy()

        window = tk.Toplevel()
        window.title("Enter Category, Brand, Type and Model")
        x = settings.get("model_menu_x", 500)
        y = settings.get("model_menu_y", 100)
        window.geometry(f"600x500+{x}+{y}")
        window.resizable(False, False)
        window.bind("<Return>", lambda event: on_ok())
        window.bind("<Escape>", lambda event: on_skip())
        window.grab_set() # Make modal
        window.focus_set()
        ttk.Label(window, text=f"Confirm/Update Product Information for\n{full_text}", font=("Arial", 14, "bold")).pack(pady=10)

        # Category Section
        ttk.Label(window, text="Category:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20, pady=(10, 5))
        frame_cat = ttk.Frame(window)
        frame_cat.pack(fill="x", padx=20, pady=5)
        ttk.Label(frame_cat, text="Input:").pack(side="left", padx=5)
        entry_category = ttk.Entry(frame_cat, width=25)
        entry_category.pack(side="left", padx=5)
        ttk.Label(frame_cat, text="Options:").pack(side="left", padx=5)
        radio_var_category = tk.StringVar(value="")
        ttk.Radiobutton(frame_cat, text=category, value=category, variable=radio_var_category).pack(side="left", padx=5)
        ttk.Radiobutton(frame_cat, text=u_category, value=u_category, variable=radio_var_category).pack(side="left", padx=5)

        # Brand Section
        ttk.Label(window, text="Brand:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20, pady=(10, 5))
        frame_brand = ttk.Frame(window)
        frame_brand.pack(fill="x", padx=20, pady=5)
        ttk.Label(frame_brand, text="Input:").pack(side="left", padx=5)
        entry_brand = ttk.Entry(frame_brand, width=25)
        entry_brand.pack(side="left", padx=5)
        ttk.Label(frame_brand, text="Options:").pack(side="left", padx=5)
        radio_var_brand = tk.StringVar(value="")
        ttk.Radiobutton(frame_brand, text=brand, value=brand, variable=radio_var_brand).pack(side="left", padx=5)
        ttk.Radiobutton(frame_brand, text=u_brand, value=u_brand, variable=radio_var_brand).pack(side="left", padx=5)

        # Type Section
        ttk.Label(window, text="Type:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20, pady=(10, 5))
        frame_type = ttk.Frame(window)
        frame_type.pack(fill="x", padx=20, pady=5)
        ttk.Label(frame_type, text="Input:").pack(side="left", padx=5)
        entry_type = ttk.Entry(frame_type, width=25)
        entry_type.pack(side="left", padx=5)
        ttk.Label(frame_type, text="Options:").pack(side="left", padx=5)
        radio_var_type = tk.StringVar(value="")
        if type:
            ttk.Radiobutton(frame_type, text=type, value=type, variable=radio_var_type).pack(side="left", padx=5)
        if u_type:
            ttk.Radiobutton(frame_type, text=u_type, value=u_type, variable=radio_var_type).pack(side="left", padx=5)

        # Model Section
        ttk.Label(window, text="Model:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20, pady=(10, 5))
        frame_model = ttk.Frame(window)
        frame_model.pack(fill="x", padx=20, pady=5)
        ttk.Label(frame_model, text="Input:").pack(side="left", padx=5)
        entry_model = ttk.Entry(frame_model, width=25)
        entry_model.pack(side="left", padx=5)
        ttk.Label(frame_model, text="Options:").pack(side="left", padx=5)
        radio_var_model = tk.StringVar(value="")
        if model:
            ttk.Radiobutton(frame_model, text=model, value=model, variable=radio_var_model).pack(side="left", padx=5)
        if u_model:
            ttk.Radiobutton(frame_model, text=u_model, value=u_model, variable=radio_var_model).pack(side="left", padx=5)

        warning_label = ttk.Label(window, text="", font=("Arial", 10))
        warning_label.pack(pady=10)

        btn_frame = ttk.Frame(window)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="OK", width=12, command=on_ok).grid(row=0, column=0, padx=10)
        ttk.Button(btn_frame, text="SKIP", width=12, command=on_skip).grid(row=0, column=1, padx=10)

        window.wait_window()

        result_category = result["category"]
        result_brand = result["brand"]
        result_type = result["type"]
        result_model = result["model"]
        return result_category, result_brand.strip().upper(), result_type, result_model.strip().upper()


    def update_model_id_in_audits_for_(self, audit_id, category, brand, model):
        truncated_model = self.get_truncated_model(model)
        result = self.find_similar_model_id(category, brand, truncated_model)
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        try:
            if result == -1:
                cursor.execute("INSERT INTO Models (category, brand, model, truncated_model) VALUES (?, ?, ?, ?)",
                    (category, brand, model, truncated_model))
                conn.commit()
                model_id = cursor.lastrowid
                cursor.execute("UPDATE Audits SET model_id = ? WHERE id = ?", (model_id, audit_id))
                conn.commit()
                print(f"New model created with id: {model_id} and audit id: {audit_id} updated.")
            else:
                model_id = result
                cursor.execute("UPDATE Audits SET model_id = ? WHERE id = ?", (model_id, audit_id))
                conn.commit()
                print(f"Existing model id: {model_id} assigned to audit id: {audit_id}.")
        except Exception as e:
            print(f"Error in update_model_id_in_audits_for_: {e}")
        finally:
            if conn:
                conn.close()

    def change_model_id_in_audits_for_(self, id, category, brand, model, truncated_model):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("SELECT id, brand, category FROM Models WHERE brand = ? AND truncated_model = ?", (brand, truncated_model))
            existing_model = cursor.fetchone()
            if existing_model:
                new_id, new_brand, new_category = existing_model
                cursor.execute("UPDATE Audits SET model_id = ? , uncertain_brand = ? , brand = ? , uncertain_category = ? , category = ? WHERE model_id = ?", (new_id, new_brand, new_brand ,new_category, new_category, id))
                conn.commit()
                cursor.execute("DELETE FROM Features WHERE model_id = ?", (new_id,))
                cursor.execute("UPDATE Features SET model_id = ? WHERE model_id = ?", (new_id, id))
                conn.commit()
                cursor.execute("DELETE FROM Models WHERE id = ?", (id,))
                conn.commit()
                print(f"Model id {id} merged into existing model id {new_id}")
                return new_id
            else:
                print(f"No existing model found with brand={brand} and truncated_model={truncated_model}")
                return id
        except sqlite3.OperationalError as e:
            print(f"Error in change_model_id_in_audits_for_: Database is locked: {e}")
            # time.sleep(1)  # Wait before retrying
            return id
        finally:
            if conn:
                conn.close()

    def get_model_id_by_(self, category, brand, model, truncated_model):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM Models WHERE brand = ? AND truncated_model = ?", (brand, truncated_model))
            existing_model = cursor.fetchone()
            if existing_model:
                model_id = existing_model[0]
                print(f"Existing model found with id: {model_id}")
                return model_id
            else:
                cursor.execute("INSERT INTO Models (category, brand, model, truncated_model) VALUES (?, ?, ?, ?)",
                    (category, brand, model, truncated_model))
                conn.commit()
                model_id = cursor.lastrowid
                print(f"New model created with id: {model_id}")
                return model_id
        except Exception as e:
            print(f"Error in get_model_id_by_: {e}")
            return -1
        finally:
            if conn:
                conn.close()


    def update_category_brand_type_for_all_model_id(self, id, category, brand, type, model, flag):
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            truncated_model = self.get_truncated_model(model)
            if flag:
                try:
                    cursor.execute("UPDATE Models SET category = ? , brand = ? , model = ?, truncated_model = ? WHERE id = ?",(category, brand, model, truncated_model, id))
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    id = self.change_model_id_in_audits_for_(id, category, brand, model, truncated_model)
            model_id = id if flag else self.get_model_id_by_(category, brand, model, truncated_model)
            cursor.execute("UPDATE Features SET value = ? WHERE model_id = ? AND name = 'type'",(type, model_id))
            conn.commit()
            query = "UPDATE Audits SET model_id = ? , category = ? , brand = ? , uncertain_category = ? , uncertain_brand = ? , uncertain_type = ? , updated_date = ? , truncated_text = ? , model = ? WHERE "+ ("model_id = ?" if flag else "id = ?")
            parameters= (model_id, category, brand, category, brand, type, current_date, model, model, id)
            cursor.execute(query, parameters)
            conn.commit()
            print(f"model/audit id: {id} category, brand, type, model have been updated successfully with values {category}, {type}, {brand}, {model}.")
            # if not flag:
            #     self.update_model_id_in_audits_for_(id, category, brand, model)
            return True
        except Exception as e:
            print("Error has occurred in update_category_brand_type_for_all_models: ", e)
            return False
        finally:
            if conn:
                conn.close()



    def manual_search_model(self, flag=True):
        query = """SELECT a.full_text, m.id AS model_id, m.model, a.brand, a.category, f.value AS type, a.uncertain_type, a.uncertain_category, a.uncertain_brand, a.model, a.truncated_text
            FROM Models m
            JOIN Features f ON f.model_id = m.id AND f.name = 'type'
            JOIN Audits a ON a.model_id = m.id
            JOIN (SELECT model_id, COUNT(*) AS total_count FROM Audits GROUP BY model_id) AS total_audits ON total_audits.model_id = m.id
            WHERE (a.uncertain_type IS NOT NULL AND a.uncertain_type != '' AND f.value != a.uncertain_type) OR (a.uncertain_category IS NOT NULL AND a.category IS NOT NULL AND a.uncertain_category != a.category) OR (a.uncertain_brand IS NOT NULL AND a.brand IS NOT NULL AND a.uncertain_brand != a.brand)
            GROUP BY m.id
            HAVING COUNT(a.id) = total_audits.total_count;"""
        query2 = """SELECT a.full_text, a.id AS audit_id, m.model, a.brand, a.category, f.value AS type, a.uncertain_type, a.uncertain_category, a.uncertain_brand, a.model, a.truncated_text
            FROM Models m
            JOIN Features f ON f.model_id = m.id AND f.name = 'type'
            JOIN Audits a ON a.model_id = m.id
            WHERE (a.uncertain_type IS NOT NULL AND a.uncertain_type != '' AND f.value != a.uncertain_type)
                OR (a.uncertain_category IS NOT NULL AND a.category IS NOT NULL AND a.uncertain_category != a.category)
                OR (a.uncertain_brand IS NOT NULL AND a.brand IS NOT NULL AND a.uncertain_brand != a.brand)
            ;"""
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute(query if flag else query2)
        rows = cursor.fetchall()
        conn.close()
        all = len(rows)
        if all == 0:
            print("There is no more row to process!")
        else:
            options = webdriver.ChromeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-extensions")
            self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            self.driver.set_page_load_timeout(8)
            time.sleep(1)
            fault = 0
            count = 0
            for row in rows:
                if fault > 5:
                    print("Automation failed due to 5 unsuccessful attempts!")
                    return
                count += 1
                full_text, id, model, brand, category, type, u_type, u_category, u_brand, a_model, u_model = row
                print(f"row number: {count}/{all}   ID: {id}")
                persian_category = (self.get_persian_category(category)+" ") if category==u_category else (self.get_persian_category(category)+" "+self.get_persian_category(u_category)+" ")
                persian_brand = (self.get_persian_brand(brand)+" ") if brand==u_brand else (self.get_persian_brand(brand)+" "+self.get_persian_brand(u_brand)+" ")
                search_engine_prompt = persian_category + persian_brand + " مدل " + model
                google_url = 'https://www.google.com/search?q=' + search_engine_prompt.strip()
                if not self.load_page_and_check(google_url):
                    print(f"failed while loading search engine prompt: {full_text}")
                    fault += 1
                    continue
                else:
                    result_category, result_brand, result_type, result_model = self.get_info_from_user_for_model(full_text, model, u_model, category, brand, type, u_category, u_brand, u_type)
                    if result_category and result_brand and result_type and result_model:
                        self.update_category_brand_type_for_all_model_id(id, result_category, result_brand, result_type, result_model, flag)
            self.driver.quit()
        if flag:
            self.manual_search_model(False)
        else:
            print("end.")

    def select_model_by_user(self, main_model: str, models: list[str]) -> str:
        result = ""
        settings = self.load_settings()
        root = tk.Toplevel()
        root.title("Merge Model with Existing Model")
        root.resizable(False, False)
        root.geometry(f"+{settings.get('merge_menu_x',500)}+{settings.get('merge_menu_y',100)}")
        root.grab_set() # Make modal - 400x300
        root.focus_set()
        root.bind("<Return>", lambda event: on_ok())
        root.bind("<Escape>", lambda event: on_end())
        # --- Fonts ---
        bold_font = tkfont.Font(root=root, weight="bold")
        # --- Main label ---
        lbl_main = tk.Label(root, text=main_model, font=bold_font)
        lbl_main.pack(padx=10, pady=(10, 5))
        # --- Radio buttons ---
        selected_var = tk.StringVar(value="__NONE__")
        radio_frame = tk.Frame(root)
        radio_frame.pack(padx=10, pady=5)
        for model in models:
            rb = tk.Radiobutton(
                radio_frame,
                text=model,
                variable=selected_var,
                value=model,
                anchor="w",
                justify="left"
            )
            rb.pack(fill="x", anchor="w")
        # --- Warning label (hidden initially) ---
        warning_label = tk.Label(
            root,
            text="",
            fg="red"
        )
        warning_label.pack(pady=(5, 0))
        # --- Button handlers ---
        def on_ok():
            settings["merge_menu_x"] = root.winfo_x()
            settings["merge_menu_y"] = root.winfo_y()
            self.save_settings(settings)
            nonlocal result
            if selected_var.get() == "__NONE__":
                result = "SKIP"
            else:
                result = selected_var.get()
            root.destroy()
        def on_end():
            settings["merge_menu_x"] = root.winfo_x()
            settings["merge_menu_y"] = root.winfo_y()
            self.save_settings(settings)
            nonlocal result
            result = ""
            root.destroy()
        # --- Buttons ---
        btn_frame = tk.Frame(root)
        btn_frame.pack(padx=10, pady=(5, 10))
        btn_ok = tk.Button(btn_frame, text="OK", width=10, command=on_ok)
        btn_ok.pack(side="left", padx=5)
        btn_end = tk.Button(btn_frame, text="END", width=10, command=on_end)
        btn_end.pack(side="left", padx=5)
        root.wait_window()
        return result

    def find_similar_truncated_models_for_(self, id: int, category:str, brand: str, model: str, truncated_model: str) -> list[tuple[int, str, str, str]]:
        _, model_ = self.find_unification_model_for(category, brand, model)
        model = model_ if model_ else model
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""SELECT id, category, brand, model
            FROM Models
            WHERE brand = ? AND (truncated_model LIKE ? OR unified_model LIKE ?) AND id != ?
        """, (brand, truncated_model[:-1]+'%', model[:-1]+'%', id))
        rows = cursor.fetchall()
        if len(rows)==0:
            cursor.execute("""SELECT id, category, brand, model
                FROM Models
                WHERE brand = ? AND (truncated_model LIKE ? OR unified_model LIKE ?) AND id != ?
            """, (brand, truncated_model[:-2]+'%', model[:-2]+'%', id))
            rows = cursor.fetchall()
        if len(rows)==0:
            cursor.execute("""SELECT id, category, brand, model
                FROM Models
                WHERE (truncated_model LIKE ? OR unified_model LIKE ?) AND id != ?
            """, (truncated_model+'%', model+'%', id))
            rows = cursor.fetchall()
        if len(rows)==0:
            cursor.execute("""SELECT id, category, brand, model
                FROM Models
                WHERE (truncated_model LIKE ? OR unified_model LIKE ?) AND id != ?
            """, (truncated_model[:-1]+'%', model[:-1]+'%', id))
            rows = cursor.fetchall()
        if len(rows)==0:
            cursor.execute("""SELECT id, category, brand, model
                FROM Models
                WHERE (truncated_model LIKE ? OR unified_model LIKE ?) AND id != ?
            """, (truncated_model[:-2]+'%', model[:-2]+'%', id))
            rows = cursor.fetchall()
        conn.close()
        return rows

    def merge_model_into_another(self, source_model_id: int, target_model_id: int):
        """
        Merge source_model_id into target_model_id.
        All audits and features from source are reassigned to target.
        Source model is then deleted.
        
        Args:
            source_model_id: Model ID to be merged (will be deleted)
            target_model_id: Model ID to merge into (will remain)
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
            # Update all Audits pointing to source to point to target
            cursor.execute(
                "UPDATE Audits SET model_id = ?, updated_date = ? WHERE model_id = ?",
                (target_model_id, current_date, source_model_id)
            )
            conn.commit()
            # Delete all features for source_model_id
            cursor.execute("""
                DELETE FROM Features WHERE model_id = ?""", (source_model_id,))
            conn.commit()
            # Delete the source model
            cursor.execute("DELETE FROM Models WHERE id = ?", (source_model_id,))
            conn.commit()
            # print(f"Model id {source_model_id} successfully merged into {target_model_id}")
            return True
        except Exception as e:
            print(f"Error in merge_model_into_another: {e}")
            return False
        finally:
            if conn:
                conn.close()

    def fill_missing_models_by_matching_to_existing_model(self):
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        cursor.execute("""SELECT m.id, m.category, m.brand, m.model, m.truncated_model
            FROM Models m
            JOIN Audits a ON a.model_id = m.id 
            LEFT JOIN Features f ON f.model_id = m.id
            GROUP BY m.id
            HAVING COUNT(DISTINCT f.name) < 4
        """)
        rows = cursor.fetchall()
        conn.close()
        for row in rows:
            id, category, brand, model, truncated_model = row
            similar_models = self.find_similar_truncated_models_for_(id, category, brand, model, truncated_model)
            if len(similar_models) > 0:
                models_list = [f"{cat} | {br} | {mod}" for (_, cat, br, mod) in similar_models]
                result = self.select_model_by_user(category+' | '+brand+' | '+model, models_list)
                if result != "" and result != "SKIP":
                    selected_index = models_list.index(result)
                    selected_model_id = similar_models[selected_index][0]
                    flag = self.merge_model_into_another(id, selected_model_id)
                    if flag:
                        print(f"Model id {id} merged into model id {selected_model_id}")
                    else:
                        print(f"Failed to merge model id {id} into model id {selected_model_id}")
                elif result == "SKIP":
                    print(f"User chose to skip merging for model id {id}")
                elif result == "":
                    print(f"User chose to end merging process.")
                    break
                    

    def get_complete_info_from_user(self, suggested_text: str, suggested_category: str, suggested_brand: str, suggested_model: str, suggested_type: str, suggested_capacity: str, suggested_technology: str, suggested_smart: str) -> tuple[str, str, str, str, str, str, str, bool]:
        """
        Opens a Tkinter form for entering complete product information.
        
        Returns:
            tuple: (full_text, category, brand, model, type, capacity, technology, smart, success)
                    success is True if OK clicked with all fields filled, False if END clicked
        """
        # Load categories from Excel
        df_categories = pd.read_excel(self.mapping_path, sheet_name="persian_cat")
        categories = df_categories["EN_CAT"].dropna().astype(str).str.strip().tolist()
        settings = self.load_settings()
        result = {
            "full_text": "",
            "category": "",
            "brand": "",
            "model": "",
            "type": "",
            "capacity": "",
            "technology": "",
            "smart": "",
            "success": False
        }
        
        def get_enum_values_from_json(category: str, feature: str) -> list:
            """Extract enum values for a feature from category JSON file"""
            json_path = os.path.join("SmartDataExtractor", "prompts", f"{category}.json")
            if not os.path.exists(json_path):
                return []
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data["function"]["parameters"]["properties"][feature].get("enum", [])
            except (KeyError, FileNotFoundError, json.JSONDecodeError):
                return []
        
        def update_feature_options(category_value):
            """Update feature combobox options based on selected category"""
            if not category_value or category_value == "":
                type_combo["values"] = []
                capacity_combo["values"] = []
                technology_combo["values"] = []
                smart_combo["values"] = []
                return
            type_options = get_enum_values_from_json(category_value, "type")
            capacity_options = get_enum_values_from_json(category_value, "capacity")
            technology_options = get_enum_values_from_json(category_value, "technology")
            smart_options = get_enum_values_from_json(category_value, "smart")
            type_combo["values"] = type_options
            capacity_combo["values"] = capacity_options
            technology_combo["values"] = technology_options
            smart_combo["values"] = smart_options
        
        def on_ok():
            full_text = entry_full_text.get().strip()
            category = category_combo.get().strip()
            brand = entry_brand.get().strip()
            model = entry_model.get().strip()
            type_val = type_combo.get().strip()
            capacity = capacity_combo.get().strip()
            technology = technology_combo.get().strip()
            smart = smart_combo.get().strip()
            
            if not (full_text or (category and brand and model)):
                warning_label.config(text="full_text OR category, brand, and model are required!", foreground="red")
                return
            
            result["full_text"] = full_text
            result["category"] = category
            result["brand"] = brand
            result["model"] = model
            result["type"] = type_val
            result["capacity"] = capacity
            result["technology"] = technology
            result["smart"] = smart
            result["success"] = True
            settings["manual_menu_x"] = window.winfo_x()
            settings["manual_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        
        def on_end():
            result["success"] = False
            settings["manual_menu_x"] = window.winfo_x()
            settings["manual_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        
        # window = tk.Tk()
        window = tk.Toplevel()
        window.title("Enter Complete Product Information")
        x = settings.get("manual_menu_x", 500)
        y = settings.get("manual_menu_y", 100)
        window.geometry(f"700x700+{x}+{y}")
        window.resizable(False, False)

        window.grab_set() # Make modal
        window.focus_set()

        window.bind("<Return>", lambda event: on_ok())
        window.bind("<Escape>", lambda event: on_end())
        
        ttk.Label(window, text="Enter Complete Product Information", font=("Arial", 14, "bold")).pack(pady=15)
        
        # Full Text
        ttk.Label(window, text="Full Text *:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_full_text = ttk.Entry(window, width=70)
        entry_full_text.pack(padx=20, pady=5)
        entry_full_text.focus()
        entry_full_text.insert(0, suggested_text)

        # Category
        ttk.Label(window, text="Category *:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        category_combo = ttk.Combobox(window, values=categories, width=67, state="readonly")
        category_combo.pack(padx=20, pady=5)
        category_combo.bind("<<ComboboxSelected>>", lambda e: update_feature_options(category_combo.get()))
        category_combo.set(suggested_category)

        # Brand
        ttk.Label(window, text="Brand *:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_brand = ttk.Entry(window, width=70)
        entry_brand.pack(padx=20, pady=5)
        entry_brand.insert(0, suggested_brand)
        
        # Model
        ttk.Label(window, text="Model *:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        entry_model = ttk.Entry(window, width=70)
        entry_model.pack(padx=20, pady=5)
        entry_model.insert(0, suggested_model)

        # Type
        ttk.Label(window, text="Type:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        type_combo = ttk.Combobox(window, width=67)
        type_combo.pack(padx=20, pady=5)
        type_combo.set(suggested_type)
        
        # Capacity
        ttk.Label(window, text="Capacity:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        capacity_combo = ttk.Combobox(window, width=67)
        capacity_combo.pack(padx=20, pady=5)
        capacity_combo.set(suggested_capacity)
        
        # Technology
        ttk.Label(window, text="Technology:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        technology_combo = ttk.Combobox(window, width=67)
        technology_combo.pack(padx=20, pady=5)
        technology_combo.set(suggested_technology)
        
        # Smart
        ttk.Label(window, text="Smart:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 2))
        smart_combo = ttk.Combobox(window, width=67)
        smart_combo.pack(padx=20, pady=5)
        smart_combo.set(suggested_smart)
        
        # Warning label
        warning_label = ttk.Label(window, text="", font=("Arial", 10))
        warning_label.pack(pady=10)
        
        # Buttons
        btn_frame = ttk.Frame(window)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="OK", width=15, command=on_ok).grid(row=0, column=0, padx=10)
        ttk.Button(btn_frame, text="END", width=15, command=on_end).grid(row=0, column=1, padx=10)
        # window.mainloop()
        update_feature_options(suggested_category)
        window.wait_window()

        return (result["full_text"], result["category"], result["brand"].upper().strip(), result["model"], 
                result["type"], result["capacity"], result["technology"], result["smart"], result["success"])

    def manual_data_filling(self, empty = False):
        suggested_rows = []
        suggested_index = 0
        if not empty:
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            cursor.execute("""SELECT a.full_text, a.brand, a.uncertain_brand, a.category, a.uncertain_category, m.model, a.model, a.uncertain_type, m.id, a.id FROM Audits a
                LEFT JOIN Models m ON a.model_id = m.id
                LEFT JOIN Features f ON m.id = f.model_id
                GROUP BY a.id
                HAVING SUM(CASE WHEN f.name IS NOT NULL THEN 1 ELSE 0 END) < 4;""")
            suggested_rows = cursor.fetchall()
            conn.close()
        while True:
            if not empty and (suggested_index >= len(suggested_rows)):
                break
            suggested_text = suggested_category = suggested_brand = suggested_model = suggested_type = suggested_capacity = suggested_technology = suggested_smart = ""
            if not empty:
                suggested_text, b, ub, c , uc, m, um, ut, m_id, a_id = suggested_rows[suggested_index]
                conn = sqlite3.connect(self.db_name, timeout=30)
                cursor = conn.cursor()
                cursor.execute("SELECT name, value FROM Features WHERE model_id = ?",(m_id,))
                f_rows = cursor.fetchall()
                if len(f_rows) == 0:
                    cursor.execute("SELECT name, value FROM Features WHERE audit_id = ?",(a_id,))
                    f_rows = cursor.fetchall()
                for f_row in f_rows:
                    name, value = f_row
                    if name == "type":
                        t = value
                        suggested_type = t if t else ut if ut else ""
                    elif name == "capacity":
                        suggested_capacity = value
                    elif name == "technology":
                        suggested_technology = value
                    elif name == "smart":
                        suggested_smart = value
                conn.close()
                suggested_category = c if c else uc if uc else ""
                suggested_brand = b if b else ub if ub else ""
                suggested_model = m if m else um if um else ""
                suggested_index +=1
            full_text, category, brand, model, type_val, capacity, technology, smart, success = self.get_complete_info_from_user(suggested_text, suggested_category, suggested_brand, suggested_model, suggested_type, suggested_capacity, suggested_technology, suggested_smart)
            # print(f"full_text={full_text} category={category} brand={brand} model={model} type_val={type_val} capacity={capacity} technology={technology} smart={smart} success={success}")
            # return
            if not success:
                print("Manual data filling ended.")
                break
            if not (full_text or (model and category and brand)):
                print("Insufficient data. Skipping this entry.")
                continue
            conn = sqlite3.connect(self.db_name, timeout=30)
            cursor = conn.cursor()
            audit_id = -1
            model_id = -1
            models_update = False
            if category and brand and model:
                models_update = True
            try:
                cursor.execute("SELECT id, model_id, truncated_text, uncertain_brand, uncertain_category, uncertain_type, category, brand, model, state FROM Audits WHERE full_text = ?", (full_text if full_text else "empty_string",))
                existing_audit = cursor.fetchone()
                current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
                if existing_audit:
                    audit_id, existing_model_id, truncated_text, uncertain_brand, uncertain_category, uncertain_type, category_, brand_, model_, state_ = existing_audit
                    truncated_text = model if model else truncated_text
                    uncertain_brand = brand if brand else uncertain_brand
                    uncertain_category = category if category else uncertain_category
                    uncertain_type = type_val if type_val else uncertain_type
                    if category or brand or model:
                        models_update = True
                    category = category if category else category_
                    brand = brand if brand else brand_
                    model = model if model else model_
                    # Determine state
                    state = state_
                    if type_val or technology or capacity or smart:
                        state = 3
                    cursor.execute("""
                        UPDATE Audits 
                        SET updated_date = ?, truncated_text = ?, uncertain_brand = ?, brand = ?, 
                            uncertain_category = ?, category = ?, uncertain_type = ?, state = ?, model = ?
                        WHERE id = ?
                    """, (current_date, truncated_text, uncertain_brand, brand, uncertain_category, 
                          category, uncertain_type, state, model, audit_id))
                    conn.commit()
                    print(f"Updated existing audit id: {audit_id}")
                    model_id = existing_model_id
                elif full_text:
                    truncated_text = model if model else full_text
                    uncertain_brand = brand if brand else None
                    uncertain_category = category if category else None
                    uncertain_type = type_val if type_val else None
                    state = 2
                    if type_val or technology or capacity or smart:
                        state = 3
                    cursor.execute("""
                        INSERT INTO Audits (full_text, updated_date, truncated_text, uncertain_brand, 
                                           brand, uncertain_category, category, uncertain_type, model, state)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (full_text, current_date, truncated_text, uncertain_brand, brand, 
                          uncertain_category, category, uncertain_type, model, state))
                    conn.commit()
                    audit_id = cursor.lastrowid
                    print(f"Inserted new audit id: {audit_id}")
                if models_update:
                    truncated_model = self.get_truncated_model(model)
                    try:
                        cursor.execute("""
                            INSERT INTO Models (category, brand, model, truncated_model)
                            VALUES (?, ?, ?, ?)
                        """, (category, brand, model, truncated_model))
                        conn.commit()
                        model_id = cursor.lastrowid
                        print(f"Inserted new model id: {model_id}")
                    except sqlite3.IntegrityError:
                        cursor.execute("""
                            SELECT id, model FROM Models 
                            WHERE brand = ? AND truncated_model = ?
                        """, (brand, truncated_model))
                        existing_model = cursor.fetchone()
                        if existing_model:
                            model_id, model_ = existing_model
                            print(f"Found existing model id: {model_id}")
                            try:
                                cursor.execute("""
                                    UPDATE Models SET category=?, brand=?, model=?, truncated_model=?
                                    WHERE brand=? AND truncated_model=?
                                """, (category, brand, model, truncated_model, brand, truncated_model))
                                conn.commit()
                                print(f"Models Updated for brand:{brand} and truncated_model:{truncated_model}")
                                if model_ != model:
                                    cursor.execute("UPDATE Audits SET updated_date = ? WHERE model_id = ?",(current_date, model_id))
                                    conn.commit()
                            except Exception:
                                print(f"Couldn't update Models for brand:{brand} and truncated_model:{truncated_model} due to integrity error!")
                        else:
                            print("Model integrity error but could not find existing model")
                if model_id and audit_id and model_id != -1 and audit_id != -1:
                    cursor.execute("UPDATE Audits SET model_id = ? , updated_date = ? WHERE id = ?", (model_id, current_date, audit_id))
                    conn.commit()
                    print(f"Updated audit id {audit_id} with model_id {model_id}")
                if ((audit_id and audit_id != -1) or (model_id and model_id != -1)) and (type_val or capacity or technology or smart):
                    if (not model_id) or model_id == -1:
                        model_id = None
                    features_to_insert = []
                    if type_val:
                        features_to_insert.append((audit_id, model_id, "type", type_val))
                    if capacity:
                        features_to_insert.append((audit_id, model_id, "capacity", capacity))
                    if technology:
                        features_to_insert.append((audit_id, model_id, "technology", technology))
                    if smart:
                        features_to_insert.append((audit_id, model_id, "smart", smart))
                    for feature_row in features_to_insert:
                        f_flag = False
                        try:
                            cursor.execute("""
                                INSERT INTO Features (audit_id, model_id, name, value)
                                VALUES (?, ?, ?, ?)
                            """, feature_row)
                            conn.commit()
                            f_flag = True
                            print(f"Feature:{feature_row[2]} Inserted for {'model_id' if model_id else 'audit_id'}:{model_id if model_id else audit_id}")
                        except Exception as e:
                            print(f"Error inserting feature: {e}")
                            try:
                                query = "UPDATE Features SET value = ? WHERE "+ ("model_id" if model_id else "audit_id")+" = ? AND name = ?"
                                cursor.execute(query, (feature_row[3], feature_row[1] if model_id else feature_row[0], feature_row[2]))
                                conn.commit()
                                f_flag = True
                                print(f"Feature:{feature_row[2]} Updated for {'model_id' if model_id else 'audit_id'}:{model_id if model_id else audit_id}")
                            except Exception:
                                print(f"Couldn't Change the value of feature:{feature_row[2]} for {'model_id' if model_id else 'audit_id'}:{model_id if model_id else audit_id} due to integrity error!")
                        if f_flag:
                            cursor.execute("UPDATE Audits SET updated_date = ? WHERE model_id = ?",(current_date, model_id))
                            conn.commit()
            except Exception as e:
                print(f"Error in manual_data_filling: {e}")
            finally:
                if conn:
                    conn.close()

    def get_unified_models_by_user(self) -> tuple[str, str, str, str, bool]:
        """
        Opens a Tkinter form for setting up similar models with unified model name.
        
        Returns:
            tuple: (category, brand, models, unified, success)
                    success is True if OK clicked with all fields filled, False if END clicked
        """
        df_categories = pd.read_excel(self.mapping_path, sheet_name="persian_cat")
        categories = df_categories["EN_CAT"].dropna().astype(str).str.strip().tolist()
        hint = "model1, model2, model3, ..."
        settings = self.load_settings()
        result = {
            "category": "",
            "brand": "",
            "models": "",
            "unified": "",
            "success": False
        }
        def on_ok():
            category = category_combo.get().strip()
            brand = entry_brand.get().strip()
            models = text_models.get("1.0", tk.END).strip()
            if hint in models:
                models = models.replace(hint, "").strip()
            unified = entry_unified.get().strip()
            if not (category and brand and models and unified):
                warning_label.config(text="All fields must be filled!", foreground="red")
                return
            if hint[:15] in models:
                warning_label.config(text="Please remove the hint text!", foreground="red")
                return
            result["category"] = category
            result["brand"] = brand.upper()
            result["models"] = models
            result["unified"] = unified
            result["success"] = True
            settings["unified_menu_x"] = window.winfo_x()
            settings["unified_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        def on_end():
            result["success"] = False
            settings["unified_menu_x"] = window.winfo_x()
            settings["unified_menu_y"] = window.winfo_y()
            self.save_settings(settings)
            window.destroy()
        window = tk.Toplevel()
        window.title("Setup Similar Models")
        x = settings.get("unified_menu_x", 500)
        y = settings.get("unified_menu_y", 100)
        window.geometry(f"600x500+{x}+{y}")
        window.resizable(False, False)
        window.grab_set()
        window.focus_set()
        # window.bind("<Return>", lambda event: on_ok())
        window.bind("<Escape>", lambda event: on_end())
        ttk.Label(window, text="Setup Similar Models", font=("Arial", 14, "bold")).pack(pady=15)
        # Category and Brand Row
        combo_brand_frame = ttk.Frame(window)
        combo_brand_frame.pack(padx=20, pady=5, fill="x")
        ttk.Label(combo_brand_frame, text="Category:").pack(side="left", padx=(0, 10))
        category_combo = ttk.Combobox(combo_brand_frame, values=categories, state="readonly", width=20)
        category_combo.pack(side="left", padx=(0, 20), fill="x", expand=True)
        ttk.Label(combo_brand_frame, text="Brand:").pack(side="left", padx=(0, 10))
        entry_brand = ttk.Entry(combo_brand_frame, width=20)
        entry_brand.pack(side="left", fill="x", expand=True)
        entry_brand.focus()
        # Models Text Box
        ttk.Label(window, text="Models (separate with ','):", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(15, 5))
        text_models = tk.Text(window, height=8, width=70, font=("Arial", 10))
        text_models.pack(padx=20, pady=5, fill="both", expand=True)
        text_models.insert("1.0", hint)
        # Unified Model
        ttk.Label(window, text="Unified Model:", font=("Arial", 10)).pack(anchor="w", padx=20, pady=(10, 5))
        entry_unified = ttk.Entry(window, width=70, font=("Arial", 10))
        entry_unified.pack(padx=20, pady=5, fill="x")
        # Warning label
        warning_label = ttk.Label(window, text="", font=("Arial", 10))
        warning_label.pack(pady=10)
        # Buttons
        btn_frame = ttk.Frame(window)
        btn_frame.pack(pady=15)
        ttk.Button(btn_frame, text="OK", width=15, command=on_ok).grid(row=0, column=0, padx=10)
        ttk.Button(btn_frame, text="END", width=15, command=on_end).grid(row=0, column=1, padx=10)
        window.wait_window()
        return result["category"], result["brand"], result["models"], result["unified"], result["success"]

    def get_correct_unified_for_(self, model_name, category, model, unified):
        tail = ''
        if category == "Refrigerator":
            if model_name.endswith('FRZ') and not unified.endswith('FRZ'):
                unified = unified.removesuffix('REF').removesuffix('-')
                unified = unified.removesuffix('TWIN').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-FRZ'
            elif model_name.endswith('REF') and not unified.endswith('REF'):
                unified = unified.removesuffix('FRZ').removesuffix('-')
                unified = unified.removesuffix('TWIN').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-REF'
            elif model_name.endswith('TWIN') and not unified.endswith('TWIN'):
                unified = unified.removesuffix('FRZ').removesuffix('-')
                unified = unified.removesuffix('REF').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-TWIN'
            elif unified.endswith('FRZ') and not model_name.endswith('FRZ'):
                unified = unified.removesuffix('REF').removesuffix('-')
                unified = unified.removesuffix('TWIN').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-FRZ'
            elif unified.endswith('REF') and not model_name.endswith('REF'):
                unified = unified.removesuffix('FRZ').removesuffix('-')
                unified = unified.removesuffix('TWIN').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-REF'
            elif unified.endswith('TWIN') and not model_name.endswith('TWIN'):
                unified = unified.removesuffix('FRZ').removesuffix('-')
                unified = unified.removesuffix('REF').removesuffix('-')
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                tail = tail.removesuffix('FRZ').removesuffix('REF').removesuffix('TWIN').removesuffix('-')
                unified = unified + tail + '-TWIN'
            elif not (unified.endswith('REF') or unified.endswith('FRZ') or unified.endswith('TWIN')) and not (model_name.endswith('REF') or model_name.endswith('FRZ') or model_name.endswith('TWIN')):
                tail = model_name.removeprefix(model)
                tail = tail if tail != model_name else ''
                unified = unified + tail
        else:
            tail = model_name.removeprefix(model)
            tail = tail if tail != model_name else ''
            unified = unified + tail
        return unified

    def unification_process_for_existing_data(self, category, brand, models_, reset_unified):
        models = models_.split(',')
        models = [model.strip() for model in models if model.strip() != '']
        select_query = "SELECT model FROM Models WHERE id = ?"
        update_query = "UPDATE Models SET unified_model = ? WHERE id = ?"
        current_date = datetime.now().strftime('%Y-%m-%d %H:%M')
        for model in models:
            tr_model = self.get_truncated_model(model)
            ids = self.find_similar_models_ids(category, brand, tr_model)
            for id in ids:
                conn = sqlite3.connect(self.db_name, timeout=30)
                cursor = conn.cursor()
                cursor.execute(select_query, (id,))
                row = cursor.fetchone()
                model_name = str(row[0])
                unified = self.get_correct_unified_for_(model_name, category, model, reset_unified)
                cursor.execute(update_query, (unified, id))
                conn.commit()
                cursor.execute("UPDATE Audits SET updated_date = ? WHERE model_id = ?", (current_date, id))
                conn.commit()
                print(f"Model: {model_name} id: {id} updated with unified model: {unified}")
                if conn:
                    conn.close()

    def setup_similar_models(self):
        while True:
            category, brand, models_, unified, success = self.get_unified_models_by_user()
            if not success:
                print("setup similar models ended.")
                break
            models_ = str(models_).upper().strip()
            brand = str(brand).upper().strip()
            reset_unified = str(unified).upper().strip()
            wb = load_workbook(self.mapping_path)
            ws = wb["models_unification"]
            ws.append([category, brand, models_, reset_unified])
            wb.save(self.mapping_path)
            # start unification process for existing data
            self.unification_process_for_existing_data(category, brand, models_, reset_unified)



    @staticmethod
    def load_settings():
        if os.path.exists("SmartDataExtractor/settings.json"):
            with open("SmartDataExtractor/settings.json", 'r', encoding='utf-8') as file:
                return json.load(file)
        return {}

    @staticmethod
    def save_settings(settings):
        with open('SmartDataExtractor/settings.json', 'w', encoding='utf-8') as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)

    
    def check_data_status(self):
        conn = sqlite3.connect(self.db_name, timeout=30)
        cursor = conn.cursor()
        try:
            # Check if any audit doesn't have a model_id
            cursor.execute("SELECT COUNT(*) FROM Audits WHERE model_id IS NULL")
            audits_without_model = cursor.fetchone()[0]
            if audits_without_model > 0:
                return -1
            # All audits have model_id, now check if all models have 4 features
            cursor.execute("""
                SELECT m.id FROM Audits a
                JOIN Models m ON a.model_id = m.id
                LEFT JOIN Features f ON f.model_id = m.id
                GROUP BY m.id
                HAVING COUNT(DISTINCT f.name) < 4
            """)
            models_incomplete = cursor.fetchall()
            if len(models_incomplete) > 0:
                return 1
            # All audits have model_id and all models have 4 features
            return 0
        except Exception as e:
            print(f"Error in check_data_status: {e}")
            return -1
        finally:
            if conn:
                conn.close()

    @staticmethod
    def user_interface():
        """Create and run the main user interface using Tkinter."""
        
        settings_file = "SmartDataExtractor/settings.json"
        
        def update_title_status():
            """Update title with status indicator based on data status"""
            status = import_table[0].check_data_status() if import_table[0] else -1
            base_text = "Smart Data Extractor - Audits"
            # colors = "🔴🟠🟡🟢🔵🟣🟤⚫⚪"
            if status == 0:
                emoji = "🔴"
            elif status == 1:
                emoji = "🟢"
            else:
                emoji = "🟡"
            title_label.config(text=f"{base_text} {emoji}")
        
        def run_in_thread(func):
            """Wrapper to run a function in a separate thread"""
            def wrapper(*args, **kwargs):
                thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
                thread.start()
            return wrapper
        
        # Load settings from file if it exists
        def load_settings():
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}
        
        # Save settings to file
        def save_settings():
            settings = load_settings()
            settings["database_name"] = entry_db_name.get()
            settings["excel_file"] = entry_excel_file.get()
            settings["sheet_name"] = entry_sheet_name.get()
            settings["output_file"] = entry_output_file.get()
            settings["output_sheet"] = entry_output_sheet.get()
            settings["main_menu_x"] = window.winfo_x()
            settings["main_menu_y"] = window.winfo_y()
            os.makedirs("SmartDataExtractor", exist_ok=True)
            try:
                with open(settings_file, 'w', encoding='utf-8') as f:
                    json.dump(settings, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error saving settings: {e}")
        
        settings = load_settings()
        import_table = [None]  # Use list to allow modification in nested functions
        
        def initiate_db():
            try:
                db_name = entry_db_name.get().strip()
                if not db_name:
                    status_label.config(text="⚠ Please enter a database name", foreground="#ff6b6b")
                    return
                import_table[0] = import_audits_table(db_name)
                update_title_status()
                status_label.config(text="✓ Database initiated successfully", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        def run_import():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                file = entry_excel_file.get().strip()
                sheet = entry_sheet_name.get().strip()
                if (file and not sheet) or (sheet and not file):
                    status_label.config(text="⚠ Please enter file and sheet name", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing...", foreground="#4dabf7")
                window.update()
                import_table[0].import_from_file_to_database(file, sheet)
                window.update()
                update_title_status()
                status_label.config(text="✓ Import completed" if file and sheet else "⚠ Sample file created!", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_import = run_in_thread(run_import)
        
        def run_manual_insert_auto():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                import_table[0].manual_data_filling()
                window.update()
                update_title_status()
                status_label.config(text="✓ Manual insert completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_manual_insert_auto = run_in_thread(run_manual_insert_auto)
        
        def run_manual_insert_empty():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                import_table[0].manual_data_filling(True)
                window.update()
                update_title_status()
                status_label.config(text="✓ Manual insert completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_manual_insert_empty = run_in_thread(run_manual_insert_empty)
        
        def run_auto_models():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing automatic models...", foreground="#4dabf7")
                window.update()
                import_table[0].extract_engine_search(1)
                window.update()
                update_title_status()
                import_table[0].state_zero_one_to_two()
                window.update()
                update_title_status()
                status_label.config(text="✓ Automatic models filling completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_auto_models = run_in_thread(run_auto_models)
        
        def run_manual_models():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing manual models...", foreground="#4dabf7")
                window.update()
                import_table[0].manual_search_model()
                window.update()
                update_title_status()
                status_label.config(text="✓ Manual models filling completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_manual_models = run_in_thread(run_manual_models)
        
        def run_auto_features():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing automatic features...", foreground="#4dabf7")
                window.update()
                import_table[0].extract_engine_search(2)
                window.update()
                update_title_status()
                import_table[0].state_two_to_three()
                window.update()
                update_title_status()
                import_table[0].complementary_search()
                window.update()
                update_title_status()
                status_label.config(text="✓ Automatic features filling completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_auto_features = run_in_thread(run_auto_features)
        
        def run_manual_features():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing manual features...", foreground="#4dabf7")
                window.update()
                import_table[0].manual_search_feature()
                window.update()
                update_title_status()
                status_label.config(text="✓ Manual features filling completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_manual_features = run_in_thread(run_manual_features)

        def run_merge_missing_models():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing merge missing models...", foreground="#4dabf7")
                window.update()
                import_table[0].fill_missing_models_by_matching_to_existing_model()
                window.update()
                update_title_status()
                status_label.config(text="✓ Merge missing models completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")

        run_merge_missing_models = run_in_thread(run_merge_missing_models)

        def run_unified_models():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Processing unified models...", foreground="#4dabf7")
                window.update()
                import_table[0].setup_similar_models()
                window.update()
                update_title_status()
                status_label.config(text="✓ Setup unified models completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_unified_models = run_in_thread(run_unified_models)
        
        def run_export():
            try:
                if not import_table[0]:
                    status_label.config(text="⚠ Please initiate database first", foreground="#ff6b6b")
                    return
                file = entry_output_file.get().strip()
                sheet = entry_output_sheet.get().strip()
                if (file and not sheet) or (not file and sheet):
                    status_label.config(text="⚠ Please enter file and sheet name", foreground="#ff6b6b")
                    return
                status_label.config(text="⏳ Exporting...", foreground="#4dabf7")
                window.update()
                import_table[0].export_data_for(file, sheet)
                window.update()
                update_title_status()
                status_label.config(text="✓ Export completed", foreground="#51cf66")
            except Exception as e:
                status_label.config(text=f"✗ Error: {str(e)[:50]}", foreground="#ff6b6b")
        
        run_export = run_in_thread(run_export)
        
        def save_and_exit():
            save_settings()
            status_label.config(text="✓ Settings saved", foreground="#51cf66")
            window.after(700, window.destroy)
        
        # Create main window
        window = tk.Tk()
        window.title("Smart Data Extractor (Audits)")
        x = settings.get("main_menu_x", 100)
        y = settings.get("main_menu_y", 100)
        window.geometry(f"700x950+{x}+{y}")
        window.configure(bg="#b4cdfa")
        window.resizable(True, True)
        
        # Create a static and flexible main frame
        main_frame = tk.Frame(window, bg="#f0f1f3")
        main_frame.pack(fill="both", expand=True, padx=0, pady=0)
        
        scrollable_frame = main_frame

        # # Create a scrollable frame
        # canvas = tk.Canvas(window, bg="#f0f1f3", highlightthickness=0)
        # scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
        # scrollable_frame = tk.Frame(canvas, bg="#f0f1f3")
        
        # scrollable_frame.bind(
        #     "<Configure>",
        #     lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        # )
        
        # canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        # canvas.configure(yscrollcommand=scrollbar.set)
        
        # canvas.pack(side="left", fill="both", expand=True)
        # scrollbar.pack(side="right", fill="y")
        
        # Define colors
        color_primary = "#4c6ef5"
        color_success = "#51cf66"
        color_warning = "#ff6b6b"
        color_bg = "#f0f1f3"
        color_card = "#ffffff"
        
        # Title
        title_label = ttk.Label(
            scrollable_frame,
            text="Smart Data Extractor - Audits",
            font=("Arial", 22, "bold"),
            background=color_bg,
            foreground=color_warning
        )
        title_label.pack(pady=(5,5))
        # update_title_status()
        # Status Section
        status_label = tk.Label(
            scrollable_frame,
            text="Ready",
            bg=color_bg,
            fg="#666666",
            font=("Arial", 9),
            pady=5
        )
        status_label.pack(fill="x")

        # Database Section
        db_frame = tk.Frame(scrollable_frame, bg=color_card, relief=tk.RAISED, bd=1)
        db_frame.pack(padx=15, pady=8, fill="x")
        
        ttk.Label(db_frame, text="Initiate Database File:", font=("Arial", 10, "bold"), background=color_card).pack(anchor="w", padx=15, pady=(3, 0))
        entry_db_frame = ttk.Frame(db_frame)
        entry_db_frame.pack(padx=12, pady=3, fill="x")
        
        entry_db_name = ttk.Entry(entry_db_frame, font=("Arial", 10), width=30)
        entry_db_name.pack(side="left", padx=(0, 5), fill="x", expand=True)
        entry_db_name.insert(0, settings.get("database_name", "retails.db"))
        # entry_db_name.pack(padx=12, pady=3, fill="x")
        
        btn_initiate = tk.Button(
            entry_db_frame,
            text="Initiate",
            command=initiate_db,
            bg=color_primary,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=32,
            pady=2,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_initiate.pack(side="left", padx=(0, 6))

        # Import Section
        temp_frame  = tk.Frame(scrollable_frame, bg=color_card, relief=tk.RAISED, bd=1)
        temp_frame.pack(padx=15, pady=5, fill="x")
        import_frame = tk.Frame(temp_frame, bg=color_card, relief=tk.RAISED, bd=1)
        import_frame.pack(padx=11, pady=5, fill="x")

        ttk.Label(import_frame, text="Import Retail Audit Texts To Database From Excel File:", font=("Arial", 10, "bold"), background=color_card).pack(anchor="w", padx=5, pady=(0, 0))
        
        ttk.Label(import_frame, text="File:", font=("Arial", 9)).pack(side="left", padx=(2, 5))
        entry_excel_file = ttk.Entry(import_frame, font=("Arial", 10), width=20)
        entry_excel_file.insert(0, settings.get("excel_file", ""))
        entry_excel_file.pack(side="left", padx=(0, 10), fill="x", expand=True)
        
        ttk.Label(import_frame, text="Sheet:", font=("Arial", 9)).pack(side="left", padx=(2, 5))
        entry_sheet_name = ttk.Entry(import_frame, font=("Arial", 10), width=15)
        entry_sheet_name.insert(0, settings.get("sheet_name", ""))
        entry_sheet_name.pack(side="left", padx=(0, 5), fill="x", expand=True)
        
        btn_import = tk.Button(
            import_frame,
            text="Run Import",
            command=run_import,
            bg=color_primary,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=20,
            pady=2,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_import.pack(padx=(0, 6), pady=(3, 5))
        
        btn_manual_insert_auto = tk.Button(
            temp_frame,
            text="Manipulate Database (Manual Insert/Update) - Auto Fill",
            command=run_manual_insert_auto,
            bg="#8159fa",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_manual_insert_auto.pack(side="left", padx=(12, 5), pady=(0, 5), fill="x", expand=True)

        btn_manual_insert_empty = tk.Button(
            temp_frame,
            text="Empty Form",
            command=run_manual_insert_empty,
            bg="#8159fa",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_manual_insert_empty.pack(side="left", padx=(0, 12), pady=(0, 5), fill="x", expand=True)
        
        # Operations Section
        ops_frame = tk.Frame(scrollable_frame, bg=color_card, relief=tk.RAISED, bd=1)
        ops_frame.pack(padx=15, pady=8, fill="x")
        
        ttk.Label(ops_frame, text="Data Operations:", font=("Arial", 10, "bold"), background=color_card).pack(anchor="w", padx=15, pady=(6, 4))
        
        buttons_frame = tk.Frame(ops_frame)
        buttons_frame.pack(pady=5, fill="x", expand=True)
        buttons_frame.grid_columnconfigure(0, weight=1)
        buttons_frame.grid_columnconfigure(1, weight=1)
        
        btn_auto_models = tk.Button(
            buttons_frame,
            text="1 - Automatic Models Filling 📡🌐",
            command=run_auto_models,
            bg="#3b82f6",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_auto_models.grid(row=0, column=0, padx=(12, 2), pady=2, sticky="nsew")
        
        btn_manual_models = tk.Button(
            buttons_frame,
            text="2 - Manual Models Filling 📡🌐",
            command=run_manual_models,
            bg="#8b5cf6",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_manual_models.grid(row=0, column=1, padx=(2, 12), pady=2, sticky="nsew")
        
        btn_auto_features = tk.Button(
            buttons_frame,
            text="3 - Automatic Features Filling 📡🌐",
            command=run_auto_features,
            bg="#3b82f6",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_auto_features.grid(row=1, column=0, padx=(12, 2), pady=(2, 2), sticky="nsew")
        
        btn_manual_features = tk.Button(
            buttons_frame,
            text="4 - Manual Features Filling 📡🌐",
            command=run_manual_features,
            bg="#8b5cf6",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_manual_features.grid(row=1, column=1, padx=(2, 12), pady=(2, 2), sticky="nsew")
        
        btn_missing_models = tk.Button(
            buttons_frame,
            text="Match Missed Models",
            command=run_merge_missing_models,
            bg="#478CCC",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_missing_models.grid(row=2, column=0, padx=(12, 2), pady=(2, 7), sticky="nsew")

        btn_unified_models = tk.Button(
            buttons_frame,
            text="Setup Similar Models",
            command=run_unified_models,
            bg="#478CCC",
            fg="white",
            font=("Arial", 10, "bold"),
            padx=5,
            pady=5,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_unified_models.grid(row=2, column=1, padx=(2, 12), pady=(2, 7), sticky="nsew")
        # Export Section
        export_frame = tk.Frame(scrollable_frame, bg=color_card, relief=tk.RAISED, bd=1)
        export_frame.pack(padx=15, pady=(4,8), fill="x")
        
        ttk.Label(export_frame, text="Export The Filled Output Excel File For:", font=("Arial", 10, "bold"), background=color_card).pack(anchor="w", padx=15, pady=(4, 0))
        
        ttk.Label(export_frame, text="File:", font=("Arial", 9)).pack(side="left", padx=(0, 5))
        entry_output_file = ttk.Entry(export_frame, font=("Arial", 10), width=20)
        entry_output_file.insert(0, settings.get("output_file", "output.xlsx"))
        entry_output_file.pack(side="left", padx=(0, 10), fill="x", expand=True)
        
        ttk.Label(export_frame, text="Sheet:", font=("Arial", 9)).pack(side="left", padx=(0, 5))
        entry_output_sheet = ttk.Entry(export_frame, font=("Arial", 10), width=15)
        entry_output_sheet.insert(0, settings.get("output_sheet", ""))
        entry_output_sheet.pack(side="left", padx=(0, 0), fill="x", expand=True)
        
        btn_export = tk.Button(
            export_frame,
            text="Run Export",
            command=run_export,
            bg=color_primary,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=18,
            pady=3,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_export.pack(padx=(4,15), pady=(0, 4))
        
        # Exit Button
        btn_exit = tk.Button(
            scrollable_frame,
            text="Save and Exit",
            command=save_and_exit,
            bg=color_warning,
            fg="white",
            font=("Arial", 10, "bold"),
            padx=15,
            pady=4,
            relief=tk.RAISED,
            cursor="hand2"
        )
        btn_exit.pack(padx=15, pady=(4,12), fill="x")

        class RedirectText(object):
            def __init__(self, text_widget):
                self.output = text_widget

            def write(self, string):
                self.output.insert(tk.END, string)
                self.output.see(tk.END)   # auto scroll
                self.output.update()  # Force immediate update

            def flush(self):
                pass  # needed for compatibility
        # Redirect stdout to a text widget
        text_frame = tk.Frame(scrollable_frame, bg=color_card, relief=tk.RAISED, bd=1)
        text_frame.pack(padx=15, pady=(0,8), fill="both", expand=True)
        ttk.Label(text_frame, text="Console Output:", font=("Arial", 10, "bold"), background=color_card).pack(anchor="w", padx=15, pady=(4, 0))
        text_console = tk.Text(text_frame, height=10, wrap="word", font=("Arial", 9))
        text_console.pack(padx=12, pady=(0, 4), fill="both", expand=True)
        redir = RedirectText(text_console)
        sys.stdout = redir

        window.mainloop()

if __name__ == '__main__':
    import_audits_table.user_interface()
    # !!!!!! important strategy: in state_zero_one_to_two() : category and brand  in state_two_to_three(): type    in load_page_and_check(): return False!!!!!!!
    # import_table = import_audits_table("retails.db")
    
    # import_table.import_from_file_to_database("255.xlsx", "255")
    # import_table.manual_data_filling()

    # import_table.extract_engine_search(1)
    # import_table.state_zero_one_to_two()
    # import_table.manual_search_model()

    # import_table.extract_engine_search(2)
    # import_table.state_two_to_three()
    # import_table.complementary_search()
    # import_table.manual_search_feature()
    
    # import_table.export_data_for("بانک.xlsx", "بانک")

