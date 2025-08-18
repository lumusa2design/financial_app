import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
from datetime import datetime
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from calendar import monthrange
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

DB_PATH = "movimientos.db"
APP_ICON = "media/1f4b2.ico"

def iso_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def iso_to_human(ts_iso: str) -> str:
    try:
        dt = datetime.strptime(ts_iso, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ts_iso

def human_to_iso(ts_human: str) -> str:
    dt = datetime.strptime(ts_human, "%d/%m/%Y %H:%M")
    return dt.strftime("%Y-%m-%d %H:%M:00")

def parse_date_only(dmy: str) -> str:
    return datetime.strptime(dmy, "%d/%m/%Y").strftime("%Y-%m-%d")

def init_db():
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movimientos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            concepto TEXT NOT NULL,
            periodicidad TEXT NOT NULL,
            tipo TEXT NOT NULL,              -- 'Gasto' o 'Entrada'
            cantidad REAL NOT NULL,
            creado_en TEXT NOT NULL          -- 'YYYY-MM-DD HH:MM:SS'
        )
    """)
    connection.commit()
    connection.close()

def save_movement(concepto, periodicidad, tipo, cantidad, creado_en=None):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    if not creado_en:
        creado_en = iso_now()
    cur.execute("""
        INSERT INTO movimientos (concepto, periodicidad, tipo, cantidad, creado_en)
        VALUES (?, ?, ?, ?, ?)
    """, (concepto, periodicidad, tipo, float(cantidad), creado_en))
    con.commit()
    rowid = cur.lastrowid
    con.close()
    return rowid, creado_en

def cargar_movimientos():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, concepto, periodicidad, tipo, cantidad, creado_en FROM movimientos ORDER BY creado_en ASC, id ASC")
    rows = cur.fetchall()
    con.close()
    return rows

def cargar_movimientos_filtrados(tipo: str, concepto: str, desde: str, hasta: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    where = []
    params = []

    if tipo != "Todos":
        where.append("tipo = ?")
        params.append(tipo)

    if concepto.strip():
        where.append("LOWER(concepto) LIKE ?")
        params.append(f"%{concepto.strip().lower()}%")

    if desde.strip():
        try:
            d = parse_date_only(desde.strip())
            where.append("creado_en >= ?")
            params.append(f"{d} 00:00:00")
        except ValueError:
            pass

    if hasta.strip():
        try:
            h = parse_date_only(hasta.strip())
            where.append("creado_en <= ?")
            params.append(f"{h} 23:59:59")
        except ValueError:
            pass

    sql = "SELECT id, concepto, periodicidad, tipo, cantidad, creado_en FROM movimientos"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY creado_en DESC, id DESC"

    cur.execute(sql, params)
    rows = cur.fetchall()
    con.close()
    return rows

def obtener_movimiento_por_id(mid: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT id, concepto, periodicidad, tipo, cantidad, creado_en FROM movimientos WHERE id=?", (mid,))
    row = cur.fetchone()
    con.close()
    return row

def actualizar_movimiento(mid: int, concepto: str, periodicidad: str, tipo: str, cantidad: float, creado_en: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE movimientos
        SET concepto=?, periodicidad=?, tipo=?, cantidad=?, creado_en=?
        WHERE id=?
    """, (concepto, periodicidad, tipo, float(cantidad), creado_en, mid))
    con.commit()
    con.close()

def eliminar_movimiento(mid: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM movimientos WHERE id=?", (mid,))
    con.commit()
    con.close()

def calcular_balance():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(
            CASE WHEN tipo='Entrada' THEN cantidad
                 WHEN tipo='Gasto'   THEN -cantidad
                 ELSE 0 END
        ), 0.0) as balance
        FROM movimientos
    """)
    (balance,) = cur.fetchone()
    con.close()
    return float(balance or 0.0)


def y_m_list_between(start_dt, end_dt):
    y, m = start_dt.year, start_dt.month
    out = []
    while (y < end_dt.year) or (y == end_dt.year and m <= end_dt.month):
        out.append((y, m))
        if m == 12:
            y += 1; m = 1
        else:
            m += 1
    return out

def monthly_fixed_projection_for_year(target_year: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""SELECT concepto, tipo, cantidad, creado_en
                   FROM movimientos WHERE periodicidad='Fijo'""")
    rows = cur.fetchall()
    con.close()

    fixed = [0.0]*12
    for concepto, tipo, cantidad, ts in rows:
        if tipo != "Gasto":
            continue
        try:
            dt0 = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt0 = datetime.fromisoformat(ts)
            except Exception:
                continue

        if dt0.year > target_year:
            continue

        start_month = dt0.month if dt0.year == target_year else 1
        for m in range(start_month, 13):
            fixed[m-1] += float(cantidad)
    return fixed

def monthly_variable_expense_series():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT cantidad, creado_en FROM movimientos
        WHERE tipo='Gasto' AND periodicidad!='Fijo'
        ORDER BY creado_en ASC
    """)
    rows = cur.fetchall()
    con.close()

    agg = {}
    for cantidad, ts in rows:
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue
        key = (dt.year, dt.month)
        agg[key] = agg.get(key, 0.0) + float(cantidad)

    keys = sorted(agg.keys())
    values = [agg[k] for k in keys]
    return keys, values

def rmse_from_residuals(residuals):
    import numpy as np
    if len(residuals) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.array(residuals)**2)))

def holt_winters_predict_next(values, horizon=12):
    import numpy as np
    values = np.asarray(values, dtype=float)
    if len(values) < 6:
        pred = [float(values.mean()) if len(values)>0 else 0.0]*horizon
        return pred, values, 0.0

    try:
        model = ExponentialSmoothing(values, trend='add', seasonal='add', seasonal_periods=12)
        res = model.fit(optimized=True, use_boxcox=False, remove_bias=False)
        pred = list(map(float, res.forecast(horizon)))
        fitted = np.asarray(res.fittedvalues, dtype=float)
        rmse = rmse_from_residuals(values - fitted[:len(values)])
        return pred, fitted, rmse
    except Exception:
        pred = [float(values[-1])] * horizon
        return pred, values, 0.0

def month_names_es():
    return ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
            "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def configurar_estilos(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    COLOR_BG_CARD  = "#F5F3FA"
    COLOR_ORCHID   = "MediumOrchid4"
    COLOR_PRIMARY  = "#2ecc71"
    COLOR_DANGER   = "#e74c3c"
    COLOR_TEXT     = "#222"
    COLOR_MUTED    = "#666"

    style.configure("TLabel", font=("Segoe UI", 10), foreground=COLOR_TEXT, background=COLOR_BG_CARD)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 14), foreground=COLOR_TEXT, background=COLOR_BG_CARD)
    style.configure("Balance.TLabel", font=("Segoe UI Semibold", 16), foreground=COLOR_TEXT, background=COLOR_BG_CARD)
    style.configure("Muted.TLabel", foreground=COLOR_MUTED, background=COLOR_BG_CARD)
    style.configure("Field.TLabel", foreground=COLOR_TEXT, background=COLOR_BG_CARD)

    style.configure("TButton", font=("Segoe UI", 10), padding=(12, 6))
    style.configure("Primary.TButton", font=("Segoe UI Semibold", 10), padding=(14, 8),
                    background=COLOR_PRIMARY, foreground="white")
    style.map("Primary.TButton",
              background=[("active", "#28b463"), ("disabled", "#a9dfbf")],
              foreground=[("disabled", "#f0f0f0")])
    style.configure("Danger.TButton", foreground=COLOR_DANGER)

    style.configure("Card.TFrame", background=COLOR_BG_CARD, relief="flat")
    style.configure("TFrame", background=COLOR_BG_CARD)
    style.configure("Toolbar.TFrame", background="#ECE8F8")
    style.configure("ToplevelCard.TFrame", background="#FFFFFF", relief="flat")

    style.configure("TEntry", padding=4, fieldbackground="#FFFFFF", background="#FFFFFF")
    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=COLOR_TEXT)
    style.map("Treeview", background=[("selected", COLOR_ORCHID)], foreground=[("selected", "white")])

def abrir_formulario(mov=None):
    editing = mov is not None
    win = tk.Toplevel(ventana)
    win.title("Editar movimiento" if editing else "Nuevo movimiento")
    win.geometry("520x390")
    win.configure(bg="MediumOrchid4")
    win.resizable(False, False)
    win.transient(ventana)
    win.grab_set()
    try:
        win.iconbitmap(APP_ICON)
    except Exception:
        pass

    concepto_var = tk.StringVar(value=mov[1] if editing else "")
    periodicidad_var = tk.StringVar(value=mov[2] if editing else "Fijo")
    tipo_var = tk.StringVar(value=mov[3] if editing else "Gasto")
    cantidad_var = tk.StringVar(value=str(mov[4]).replace(".", ",") if editing else "")
    fecha_var = tk.StringVar(value=iso_to_human(mov[5]) if editing else datetime.now().strftime("%d/%m/%Y %H:%M"))

    card = ttk.Frame(win, style="ToplevelCard.TFrame")
    card.place(relx=0.5, rely=0.5, anchor="center")
    card.grid_columnconfigure(1, weight=1)
    pad = {'padx': 14, 'pady': 8}

    ttk.Label(card, text="Editar movimiento" if editing else "Añadir movimiento", style="Title.TLabel")\
        .grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 4))
    ttk.Separator(card, orient="horizontal").grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))

    ttk.Label(card, text="Concepto / descripción:", style="Field.TLabel")\
        .grid(row=2, column=0, sticky="w", **pad)
    entry_concepto = ttk.Entry(card, textvariable=concepto_var, width=32)
    entry_concepto.grid(row=2, column=1, sticky="ew", **pad)

    ttk.Label(card, text="Periodicidad:", style="Field.TLabel")\
        .grid(row=3, column=0, sticky="w", **pad)
    frame_period = ttk.Frame(card)
    frame_period.grid(row=3, column=1, sticky="w", **pad)
    ttk.Radiobutton(frame_period, text="Fijo", variable=periodicidad_var, value="Fijo")\
        .pack(side="left")
    ttk.Radiobutton(frame_period, text="Variable", variable=periodicidad_var, value="Variable")\
        .pack(side="left", padx=(12, 0))

    ttk.Label(card, text="Tipo:", style="Field.TLabel")\
        .grid(row=4, column=0, sticky="w", **pad)
    frame_tipo = ttk.Frame(card)
    frame_tipo.grid(row=4, column=1, sticky="w", **pad)
    ttk.Radiobutton(frame_tipo, text="Gasto", variable=tipo_var, value="Gasto")\
        .pack(side="left")
    ttk.Radiobutton(frame_tipo, text="Entrada", variable=tipo_var, value="Entrada")\
        .pack(side="left", padx=(12, 0))


    ttk.Label(card, text="Cantidad:", style="Field.TLabel")\
        .grid(row=5, column=0, sticky="w", **pad)
    monto_frame = ttk.Frame(card)
    monto_frame.grid(row=5, column=1, sticky="w", **pad)
    ttk.Label(monto_frame, text="€").pack(side="left", padx=(0, 6))
    entry_cantidad = ttk.Entry(monto_frame, textvariable=cantidad_var, width=20)
    entry_cantidad.pack(side="left")

    ttk.Label(card, text="Fecha (dd/MM/yyyy HH:mm):", style="Field.TLabel")\
        .grid(row=6, column=0, sticky="w", **pad)
    entry_fecha = ttk.Entry(card, textvariable=fecha_var, width=20)
    entry_fecha.grid(row=6, column=1, sticky="w", **pad)

    ttk.Separator(card, orient="horizontal").grid(row=7, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 0))

    actions = ttk.Frame(card, style="ToplevelCard.TFrame")
    actions.grid(row=8, column=0, columnspan=2, sticky="ew")
    actions.grid_columnconfigure(0, weight=1)

    def guardar():
        concepto = concepto_var.get().strip()
        periodicidad = periodicidad_var.get()
        tipo = tipo_var.get()
        cantidad_txt = cantidad_var.get().strip().replace(",", ".")
        fecha_txt = fecha_var.get().strip()

        if not concepto:
            messagebox.showerror("Faltan datos", "El concepto no puede estar vacío.", parent=win)
            entry_concepto.focus_set()
            return
        try:
            cantidad = float(cantidad_txt)
        except ValueError:
            messagebox.showerror("Cantidad inválida", "Introduce una cantidad numérica (p. ej., 123.45).", parent=win)
            entry_cantidad.focus_set()
            return
        try:
            fecha_iso = human_to_iso(fecha_txt)
        except ValueError:
            messagebox.showerror("Fecha inválida", "Usa el formato dd/MM/yyyy HH:mm.", parent=win)
            entry_fecha.focus_set()
            return

        try:
            if editing:
                actualizar_movimiento(mov[0], concepto, periodicidad, tipo, cantidad, fecha_iso)
            else:
                save_movement(concepto, periodicidad, tipo, cantidad, creado_en=fecha_iso)
        except Exception as e:
            messagebox.showerror("Error guardando", f"No se pudo guardar el movimiento.\n\n{e}", parent=win)
            return


        materializar_fijos()
        if current_view.get() == "Resumen":
            refrescar_balance_y_grafica()
        else:
            refrescar_listado()

        win.destroy()

    def cancelar():
        win.destroy()

    btn_cancelar = ttk.Button(actions, text="Cancelar", command=cancelar)
    btn_guardar = ttk.Button(actions, text="Guardar", style="Primary.TButton", command=guardar)
    materializar_fijos()
    if current_view.get() == "Resumen":
        refrescar_balance_y_grafica()
    else:
        refrescar_listado()
    btn_cancelar.grid(row=0, column=0, sticky="w", padx=(14, 6), pady=14)
    btn_guardar.grid(row=0, column=1, sticky="e", padx=(6, 14), pady=14)

    entry_concepto.focus_set()
    win.bind("<Return>", lambda e: guardar())
    win.bind("<Escape>", lambda e: cancelar())

def agrupar_entradas_gastos(movs, modo="Mes"):

    from collections import defaultdict
    from datetime import timedelta

    def parse_ts(ts):
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.fromisoformat(ts)

    ahora = datetime.now()
    datos = []
    for _id, _c, _p, tipo, cantidad, ts in movs:
        try:
            dt = parse_ts(ts)
        except Exception:
            continue
        datos.append((dt, tipo, float(cantidad)))

    if modo == "Semana":
        year, week, _ = ahora.isocalendar()
        lunes = datetime.fromisocalendar(year, week, 1).date()
        dias = [lunes + timedelta(days=i) for i in range(7)]
        etiquetas = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        e_map = defaultdict(float); g_map = defaultdict(float)
        for dt, tipo, cant in datos:
            d = dt.date()
            if d in dias:
                (e_map if tipo=="Entrada" else g_map)[d] += cant
        entradas = [e_map.get(d, 0.0) for d in dias]
        gastos   = [g_map.get(d, 0.0) for d in dias]

    elif modo == "Año":
        e_map = defaultdict(float); g_map = defaultdict(float)
        for dt, tipo, cant in datos:
            y = dt.year
            (e_map if tipo=="Entrada" else g_map)[y] += cant
        years = sorted(set(e_map)|set(g_map))
        if len(years) > 6: years = years[-6:]
        etiquetas = [str(y) for y in years]
        entradas  = [e_map.get(y,0.0) for y in years]
        gastos    = [g_map.get(y,0.0) for y in years]

    else:
        year = ahora.year
        etiquetas = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                     "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
        e = [0.0]*12; g = [0.0]*12
        for dt, tipo, cant in datos:
            if dt.year == year:
                i = dt.month - 1
                if tipo == "Entrada": e[i] += cant
                else: g[i] += cant
        entradas, gastos = e, g

    return etiquetas, entradas, gastos

def predecir_gasto_mensual(cost_per_month, window=6):
    prediction = [0.0]*12
    for i in range(12):
        past = [cost_per_month[j] for j in range(max(0, i-window), i)]
        if past:
            prediction[i] = sum(past)/len(past)
        else:
            prediction[i] = 0.0
    return prediction


def dibujar_grafica(modo):
    movs = cargar_movimientos()
    ax.clear()

    COLOR_ENTRADAS = "#2ecc71"
    COLOR_GASTOS   = "#e74c3c"
    COLOR_PREV     = "#6c5ce7"
    COLOR_BANDA    = "#b3a6ff"

    etiquetas, entradas, gastos = agrupar_entradas_gastos(movs, modo=modo)

    if not etiquetas:
        ax.set_title(f"Entradas vs Gastos por {modo.lower()}", color="#333")
        ax.set_ylabel("€", color="#333")
        ax.text(0.5, 0.5, "Sin datos", ha="center", va="center",
                transform=ax.transAxes, color="#666")
        ax.grid(axis='y', linestyle='--', alpha=0.35, color="#bbb")
        fig.tight_layout(); canvas.draw_idle()
        return

    x = list(range(len(etiquetas)))
    ax.bar(x, entradas, label="Entradas", color=COLOR_ENTRADAS, alpha=0.70, edgecolor="#1e8449")
    ax.bar(x, gastos,   label="Gastos",   color=COLOR_GASTOS,   alpha=0.55, edgecolor="#943126")

    if modo == "Mes":
        ahora = datetime.now()
        year = ahora.year
        keys_hist, val_hist = monthly_variable_expense_series()
        pred_var_12, fitted, rmse = holt_winters_predict_next(val_hist, horizon=12)
        meses = [(year, m) for m in range(1, 13)]
        start_month_index = ahora.month - 1
        steps_remaining = 12 - start_month_index
        pred_var_al_year = [0.0]*12
        for i in range(steps_remaining):
            pred_var_al_year[start_month_index + i] = pred_var_12[i]


        proj_fijos = monthly_fixed_projection_for_year(year)

        linea = [0.0]*12
        for i in range(12):
            if i < start_month_index:
                linea[i] = gastos[i]
            else:
                linea[i] = pred_var_al_year[i] + proj_fijos[i]

        banda_inf = [None]*12
        banda_sup = [None]*12
        for i in range(12):
            if i >= start_month_index:
                banda_inf[i] = max(0.0, linea[i] - rmse)
                banda_sup[i] = linea[i] + rmse
        ax.plot(x, linea, marker="o", linewidth=2.0, label="Predicción gasto total", color=COLOR_PREV)
        if steps_remaining > 0 and rmse > 0:
            xs = x[start_month_index:]
            ys_low = [banda_inf[i] for i in range(start_month_index, 12)]
            ys_up  = [banda_sup[i]  for i in range(start_month_index, 12)]
            ax.fill_between(xs, ys_low, ys_up, alpha=0.25, color=COLOR_BANDA, label="±RMSE")

    subt = ("por mes (año actual)" if modo == "Mes"
            else "de esta semana" if modo == "Semana"
            else "por año")
    ax.set_title(f"Entradas vs Gastos {subt}", color="#333")
    ax.set_ylabel("€", color="#333")
    ax.set_xticks(x)
    ax.set_xticklabels(etiquetas, rotation=45, ha="right", color="#333")
    ax.grid(axis='y', linestyle='--', alpha=0.35, color="#bbb")
    ax.legend(facecolor="#FFFFFF", edgecolor="#ddd")
    ax.axhline(0, linewidth=1, color="#999")

    fig.tight_layout()
    canvas.draw_idle()



def refrescar_balance_y_grafica():
    bal = calcular_balance()
    balance_str = f"{bal:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    balance_val_label.config(text=balance_str)
    modo = combo_modo.get()
    dibujar_grafica(modo)

def build_listado(parent):
    toolbar = ttk.Frame(parent, style="Toolbar.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    for i in range(7):
        toolbar.grid_columnconfigure(i, weight=0)
    toolbar.grid_columnconfigure(7, weight=1)

    ttk.Label(toolbar, text="Tipo:").grid(row=0, column=0, sticky="w")
    filtro_tipo = ttk.Combobox(toolbar, state="readonly", values=["Todos", "Gasto", "Entrada"], width=10)
    filtro_tipo.grid(row=0, column=1, padx=(4, 12))
    filtro_tipo.set("Todos")

    ttk.Label(toolbar, text="Buscar:").grid(row=0, column=2, sticky="w")
    filtro_concepto = ttk.Entry(toolbar, width=22)
    filtro_concepto.grid(row=0, column=3, padx=(4, 12))

    ttk.Label(toolbar, text="Desde (dd/MM/yyyy):").grid(row=0, column=4, sticky="w")
    filtro_desde = ttk.Entry(toolbar, width=12)
    filtro_desde.grid(row=0, column=5, padx=(4, 8))

    ttk.Label(toolbar, text="Hasta:").grid(row=0, column=6, sticky="w")
    filtro_hasta = ttk.Entry(toolbar, width=12)
    filtro_hasta.grid(row=0, column=7, padx=(4, 8))

    btn_aplicar = ttk.Button(toolbar, text="Aplicar", style="Primary.TButton")
    btn_limpiar = ttk.Button(toolbar, text="Limpiar")
    btn_aplicar.grid(row=0, column=8, padx=(8, 4))
    btn_limpiar.grid(row=0, column=9, padx=(4, 0))

    btn_add = ttk.Button(toolbar, text="Añadir", style="Primary.TButton", command=lambda: abrir_formulario(None))
    btn_edit = ttk.Button(toolbar, text="Editar")
    btn_del = ttk.Button(toolbar, text="Eliminar", style="Danger.TButton")
    btn_add.grid(row=0, column=10, padx=(16, 0))
    btn_edit.grid(row=0, column=11, padx=(6, 0))
    btn_del.grid(row=0, column=12, padx=(6, 0))

    cols = ("id", "fecha", "concepto", "periodicidad", "tipo", "cantidad")
    tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="browse")
    tree.grid(row=1, column=0, sticky="nsew")  # se escala

    vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
    tree.configure(yscroll=vsb.set, xscroll=hsb.set)
    vsb.grid(row=1, column=1, sticky="ns")
    hsb.grid(row=2, column=0, sticky="ew")

    tree.heading("id", text="ID")
    tree.heading("fecha", text="Fecha")
    tree.heading("concepto", text="Concepto")
    tree.heading("periodicidad", text="Periodicidad")
    tree.heading("tipo", text="Tipo")
    tree.heading("cantidad", text="Cantidad (€)")

    tree.column("id", width=60, anchor="center")
    tree.column("fecha", width=150, anchor="center")
    tree.column("concepto", width=260)
    tree.column("periodicidad", width=100, anchor="center")
    tree.column("tipo", width=90, anchor="center")
    tree.column("cantidad", width=110, anchor="e")

    def formato_eur(x: float) -> str:
        s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return s

    def cargar_en_tree():
        for i in tree.get_children():
            tree.delete(i)
        filas = cargar_movimientos_filtrados(
            filtro_tipo.get(),
            filtro_concepto.get(),
            filtro_desde.get(),
            filtro_hasta.get()
        )
        for id_, concepto, periodicidad, tipo, cantidad, ts in filas:
            fecha_fmt = iso_to_human(ts)
            cant = cantidad if tipo == "Entrada" else -cantidad
            tree.insert("", "end", iid=str(id_), values=(id_, fecha_fmt, concepto, periodicidad, tipo, formato_eur(cant)))

    def limpiar_filtros():
        filtro_tipo.set("Todos")
        filtro_concepto.delete(0, "end")
        filtro_desde.delete(0, "end")
        filtro_hasta.delete(0, "end")
        cargar_en_tree()

    def get_sel_id():
        sel = tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def editar_sel(_e=None):
        mid = get_sel_id()
        if mid is None:
            messagebox.showinfo("Editar", "Selecciona un movimiento primero.", parent=parent)
            return
        mov = obtener_movimiento_por_id(mid)
        if mov is None:
            messagebox.showerror("Error", "No se encontró el movimiento.", parent=parent)
            return
        abrir_formulario(mov)

    def eliminar_sel(_e=None):
        mid = get_sel_id()
        if mid is None:
            messagebox.showinfo("Eliminar", "Selecciona un movimiento primero.", parent=parent)
            return
        mov = obtener_movimiento_por_id(mid)
        if mov is None:
            messagebox.showerror("Error", "No se encontró el movimiento.", parent=parent)
            return
        concepto_preview = (mov[1] or "")[:30]
        if not messagebox.askyesno("Confirmar eliminación",
                                   f"¿Seguro que quieres eliminar el movimiento #{mid}?\n\n{concepto_preview}",
                                   parent=parent):
            return
        try:
            eliminar_movimiento(mid)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo eliminar.\n\n{e}", parent=parent)
            return
        cargar_en_tree()
        refrescar_balance_y_grafica()

    btn_aplicar.config(command=cargar_en_tree)
    btn_limpiar.config(command=limpiar_filtros)
    btn_edit.config(command=editar_sel)
    btn_del.config(command=eliminar_sel)
    tree.bind("<Double-1>", editar_sel)
    tree.bind("<Delete>", eliminar_sel)
    filtro_concepto.bind("<Return>", lambda e: cargar_en_tree())
    filtro_desde.bind("<Return>", lambda e: cargar_en_tree())
    filtro_hasta.bind("<Return>", lambda e: cargar_en_tree())
    filtro_tipo.bind("<<ComboboxSelected>>", lambda e: cargar_en_tree())

    cargar_en_tree()

    parent.grid_rowconfigure(1, weight=1)
    parent.grid_columnconfigure(0, weight=1)

    parent.tree = tree
    parent.reload = cargar_en_tree

def refrescar_listado():
    if content_frame and hasattr(content_frame, "reload"):
        content_frame.reload()

def clear_content():
    for w in content_frame.winfo_children():
        w.destroy()

def show_resumen():
    current_view.set("Resumen")
    clear_content()

    titulo = ttk.Label(content_frame, text="Resumen financiero", style="Title.TLabel")
    titulo.grid(row=0, column=0, sticky="w", columnspan=2)

    balance_label = ttk.Label(content_frame, text="Balance acumulado:", style="Muted.TLabel")
    balance_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
    global balance_val_label
    balance_val_label = ttk.Label(content_frame, text="0,00 €", style="Balance.TLabel")
    balance_val_label.grid(row=2, column=0, sticky="w", pady=(0, 8))

    controles = ttk.Frame(content_frame)
    controles.grid(row=3, column=0, sticky="w", pady=(8, 8))
    ttk.Label(controles, text="Agrupar por:").grid(row=0, column=0, sticky="w", padx=(0, 8))
    global combo_modo
    combo_modo = ttk.Combobox(controles, state="readonly", values=["Semana", "Mes", "Año"], width=10)
    combo_modo.grid(row=0, column=1, sticky="w")
    combo_modo.set("Mes")

    btn_refrescar = ttk.Button(controles, text="Refrescar", command=lambda: dibujar_grafica(combo_modo.get()))
    btn_refrescar.grid(row=0, column=2, padx=(8, 0))

    btn_nuevo = ttk.Button(controles, text="Añadir movimiento", style="Primary.TButton", command=lambda: abrir_formulario(None))
    btn_nuevo.grid(row=0, column=3, padx=(16, 0))

    global fig, ax, canvas
    fig = Figure(figsize=(7.5, 3.8), dpi=100)
    fig.patch.set_facecolor("#F5F3FA")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#FFFFFF")
    ax.tick_params(colors="#333")
    for spine in ax.spines.values():
        spine.set_color("#888")

    canvas = FigureCanvasTkAgg(fig, master=content_frame)
    canvas_widget = canvas.get_tk_widget()
    canvas_widget.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))

    content_frame.grid_rowconfigure(4, weight=1)
    content_frame.grid_columnconfigure(0, weight=1)

    refrescar_balance_y_grafica()
    combo_modo.bind("<<ComboboxSelected>>", lambda e: dibujar_grafica(combo_modo.get()))

def show_movimientos():
    current_view.set("Movimientos")
    clear_content()
    build_listado(content_frame)



def _month_key(dt):
    return (dt.year, dt.month)

def _month_first_day(year, month):
    return datetime(year, month, 1, 0, 0, 1)

def _month_clamp_day(year, month, day):
    last = monthrange(year, month)[1]
    return min(day, last)

def materializar_fijos():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""SELECT id, concepto, periodicidad, tipo, cantidad, creado_en
                   FROM movimientos WHERE periodicidad='Fijo' ORDER BY creado_en ASC""")
    fijos = cur.fetchall()

    if not fijos:
        con.close()
        return

    hoy = datetime.now()
    for (mid, concepto, periodicidad, tipo, cantidad, ts) in fijos:
        try:
            dt0 = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt0 = datetime.fromisoformat(ts)
            except Exception:
                continue

        y, m = dt0.year, dt0.month
        while (y < hoy.year) or (y == hoy.year and m <= hoy.month):
            dia = _month_clamp_day(y, m, dt0.day)
            fecha_obj = datetime(y, m, dia, dt0.hour, dt0.minute, dt0.second)

            mes_ini = datetime(y, m, 1, 0, 0, 0).strftime("%Y-%m-%d %H:%M:%S")
            mes_fin = datetime(y, m, monthrange(y, m)[1], 23, 59, 59).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("""
                SELECT 1 FROM movimientos
                WHERE periodicidad='Fijo' AND concepto=? AND tipo=? AND cantidad=?
                  AND creado_en BETWEEN ? AND ? LIMIT 1
            """, (concepto, tipo, float(cantidad), mes_ini, mes_fin))
            existe = cur.fetchone() is not None

            if not existe:
                cur.execute("""INSERT INTO movimientos
                               (concepto, periodicidad, tipo, cantidad, creado_en)
                               VALUES (?, 'Fijo', ?, ?, ?)""",
                            (concepto, tipo, float(cantidad),
                             fecha_obj.strftime("%Y-%m-%d %H:%M:%S")))
            if m == 12:
                y += 1; m = 1
            else:
                m += 1

    con.commit()
    con.close()


ventana = tk.Tk()
ventana.title("financial app")
ventana.geometry("1100x680")
ventana.minsize(820, 560)
try:
    ventana.iconbitmap(APP_ICON)
except Exception:
    pass
ventana.configure(bg="SeaGreen4")

configurar_estilos(ventana)
init_db()
materializar_fijos()


ventana.grid_rowconfigure(0, weight=1)
ventana.grid_columnconfigure(0, weight=1)

card_main = ttk.Frame(ventana, style="Card.TFrame", padding=16)
card_main.grid(row=0, column=0, sticky="nsew")
card_main.grid_rowconfigure(1, weight=1)
card_main.grid_columnconfigure(0, weight=1)

topbar = ttk.Frame(card_main, style="Toolbar.TFrame", padding=(8, 6))
topbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
topbar.grid_columnconfigure(0, weight=1)

current_view = tk.StringVar(value="Resumen")

btn_resumen = ttk.Button(topbar, text="Resumen", command=show_resumen)
btn_resumen.grid(row=0, column=1, padx=(0, 6))
btn_movs = ttk.Button(topbar, text="Movimientos", command=show_movimientos)
btn_movs.grid(row=0, column=2, padx=(6, 0))

content_frame = ttk.Frame(card_main, style="Card.TFrame")
content_frame.grid(row=1, column=0, sticky="nsew")

show_resumen()

ventana.mainloop()
