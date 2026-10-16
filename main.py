import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import csv
import io

R = 20
MAX_VERTICES = 100
MAX_EDGES = 500


class GraphEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Построение графа — FR-7…FR-11")
        self.root.geometry("1250x820")
        self.root.minsize(950, 620)

        self.vertices = {}       # id -> (x, y)
        self.edges = []          # список дуг/рёбер (u, v); порядок важен для орграфа
        self.next_vertex_id = 1
        self.selected = None
        self.dragging = None
        self.mode = "add"
        self.highlight_edges = set()

        self.directed_var = tk.BooleanVar(value=False)
        self.allow_multi_var = tk.BooleanVar(value=False)

        self._build_ui()
        self.refresh_all()
        self.redraw()

    # ---------------- UI ----------------
    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(side="top", fill="x")

        ttk.Label(toolbar, text="Тип графа:").pack(side="left", padx=(0, 4))
        ttk.Radiobutton(
            toolbar, text="Неориентированный", variable=self.directed_var,
            value=False, command=self.on_graph_type_changed
        ).pack(side="left")
        ttk.Radiobutton(
            toolbar, text="Ориентированный", variable=self.directed_var,
            value=True, command=self.on_graph_type_changed
        ).pack(side="left", padx=(0, 10))
        ttk.Checkbutton(
            toolbar, text="Разрешить петли и кратные рёбра",
            variable=self.allow_multi_var, command=self.refresh_all
        ).pack(side="left", padx=4)

        buttons = ttk.Frame(self.root, padding=(5, 0, 5, 5))
        buttons.pack(fill="x")
        for text, value in [
            ("Добавить вершины", "add"),
            ("Соединить рёбра", "edge"),
            ("Перемещать вершины", "move"),
            ("Удалить ребро", "delete_edge"),
            ("Удалить вершину", "delete_vertex"),
        ]:
            ttk.Button(buttons, text=text, command=lambda m=value: self.set_mode(m)).pack(side="left", padx=2)

        ttk.Button(buttons, text="Открыть JSON", command=self.load_json).pack(side="right", padx=2)
        ttk.Button(buttons, text="Сохранить JSON", command=self.save_json).pack(side="right", padx=2)

        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=5, pady=5)
        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        self.canvas = tk.Canvas(left, bg="white", highlightthickness=1, highlightbackground="#aaa")
        self.canvas.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="Режим: добавление вершин")
        ttk.Label(left, textvariable=self.status_var, anchor="w").pack(fill="x", pady=(4, 0))
        self.stats_var = tk.StringVar()
        ttk.Label(left, textvariable=self.stats_var, justify="left", anchor="w").pack(fill="x", pady=4)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        degree_tab = ttk.Frame(self.notebook, padding=5)
        adj_tab = ttk.Frame(self.notebook, padding=5)
        inc_tab = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(degree_tab, text="Степени и лемма")
        self.notebook.add(adj_tab, text="Матрица смежности")
        self.notebook.add(inc_tab, text="Матрица инцидентности")

        self.degree_tree = ttk.Treeview(
            degree_tab, columns=("vertex", "in", "out", "degree"),
            show="headings", height=10
        )
        for col, title, width in [
            ("vertex", "Вершина", 80), ("in", "deg−", 70),
            ("out", "deg+", 70), ("degree", "Степень", 80)
        ]:
            self.degree_tree.heading(col, text=title)
            self.degree_tree.column(col, width=width, anchor="center")
        self.degree_tree.pack(fill="both", expand=True)
        self.lemma_var = tk.StringVar()
        ttk.Label(degree_tab, textvariable=self.lemma_var, justify="left", anchor="w").pack(fill="x", pady=8)

        self.adj_tree = ttk.Treeview(adj_tab, show="tree headings", height=16)
        self.adj_tree.pack(fill="both", expand=True)
        self.adj_tree.bind("<ButtonRelease-1>", self.on_adjacency_cell_click)
        ttk.Button(adj_tab, text="Копировать матрицу смежности (CSV)",
                   command=self.copy_adjacency_csv).pack(fill="x", pady=(5, 0))

        self.inc_text = tk.Text(inc_tab, wrap="none", font=("Consolas", 10), state="disabled", height=18)
        inc_y = ttk.Scrollbar(inc_tab, orient="vertical", command=self.inc_text.yview)
        inc_x = ttk.Scrollbar(inc_tab, orient="horizontal", command=self.inc_text.xview)
        self.inc_text.configure(yscrollcommand=inc_y.set, xscrollcommand=inc_x.set)
        self.inc_text.grid(row=0, column=0, sticky="nsew")
        inc_y.grid(row=0, column=1, sticky="ns")
        inc_x.grid(row=1, column=0, sticky="ew")
        inc_tab.rowconfigure(0, weight=1)
        inc_tab.columnconfigure(0, weight=1)
        ttk.Button(inc_tab, text="Копировать матрицу инцидентности (CSV)",
                   command=self.copy_incidence_csv).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.canvas_release)

    # ---------------- Основная логика ----------------
    @property
    def directed(self):
        return self.directed_var.get()

    def canonical_edge(self, u, v):
        return (u, v) if self.directed else tuple(sorted((u, v)))

    def edge_exists(self, u, v):
        candidate = self.canonical_edge(u, v)
        return any(self.canonical_edge(a, b) == candidate for a, b in self.edges)

    def vertex_at(self, x, y):
        for v, (vx, vy) in self.vertices.items():
            if (x - vx) ** 2 + (y - vy) ** 2 <= R ** 2:
                return v
        return None

    def set_mode(self, mode):
        self.mode = mode
        self.selected = None
        self.dragging = None
        labels = {
            "add": "Добавление вершин",
            "edge": "Соединение рёбер: выберите две вершины",
            "move": "Перемещение вершин",
            "delete_edge": "Удаление ребра: выберите две вершины",
            "delete_vertex": "Удаление вершин",
        }
        self.status_var.set("Режим: " + labels[mode])
        self.redraw()

    def on_graph_type_changed(self):
        # Вершины и рёбра остаются на месте; меняется их интерпретация.
        self.selected = None
        self.highlight_edges.clear()
        self.redraw()
        self.refresh_all()

    def canvas_click(self, event):
        x, y = event.x, event.y
        v = self.vertex_at(x, y)

        if self.mode == "add":
            if v is not None:
                return
            if len(self.vertices) >= MAX_VERTICES:
                messagebox.showwarning("Ограничение", f"Максимум {MAX_VERTICES} вершин.")
                return
            for vx, vy in self.vertices.values():
                if (x - vx) ** 2 + (y - vy) ** 2 < (2 * R + 5) ** 2:
                    messagebox.showinfo("Граф", "Вершины слишком близко.")
                    return
            self.vertices[self.next_vertex_id] = (x, y)
            self.next_vertex_id += 1
            self.refresh_all()
            self.redraw()
            return

        if v is None:
            self.selected = None
            self.redraw()
            return

        if self.mode in ("edge", "delete_edge"):
            if self.selected is None:
                self.selected = v
            elif self.selected == v:
                if self.mode == "edge" and self.allow_multi_var.get():
                    self.add_edge(v, v)
                else:
                    self.selected = None
            elif self.mode == "edge":
                self.add_edge(self.selected, v)
                self.selected = None
            else:
                matching = [
                    i for i, (a, b) in enumerate(self.edges)
                    if self.canonical_edge(a, b) == self.canonical_edge(self.selected, v)
                ]
                if matching:
                    self.edges.pop(matching[-1])
                    self.highlight_edges.clear()
                    self.refresh_all()
                else:
                    messagebox.showinfo("Граф", "Между выбранными вершинами нет ребра/дуги.")
                self.selected = None
            self.redraw()
            return

        if self.mode == "delete_vertex":
            del self.vertices[v]
            self.edges = [(a, b) for a, b in self.edges if a != v and b != v]
            self.highlight_edges.clear()
            self.selected = None
            self.refresh_all()
            self.redraw()
            return

        if self.mode == "move":
            self.dragging = v
            self.selected = v
            self.redraw()

    def add_edge(self, u, v):
        if len(self.edges) >= MAX_EDGES:
            messagebox.showwarning("Ограничение", f"Максимум {MAX_EDGES} рёбер/дуг.")
            return
        if not self.allow_multi_var.get():
            if u == v:
                messagebox.showinfo("Простой граф", "Петли отключены. Включите соответствующую галочку.")
                return
            if self.edge_exists(u, v):
                messagebox.showinfo("Простой граф", "Такое ребро/дуга уже существует.")
                return
        self.edges.append((u, v))
        self.highlight_edges.clear()
        self.refresh_all()
        self.redraw()

    def canvas_drag(self, event):
        if self.mode != "move" or self.dragging is None:
            return
        x, y = event.x, event.y
        for v, (vx, vy) in self.vertices.items():
            if v != self.dragging and (x - vx) ** 2 + (y - vy) ** 2 < (2 * R + 5) ** 2:
                return
        self.vertices[self.dragging] = (x, y)
        self.redraw()

    def canvas_release(self, event):
        self.dragging = None
        self.refresh_all()

    # ---------------- Рисование ----------------
    def draw_edge(self, index, a, b):
        x1, y1 = self.vertices[a]
        x2, y2 = self.vertices[b]
        highlighted = index in self.highlight_edges
        color = "#e53935" if highlighted else "#333333"
        width = 4 if highlighted else 2

        if a == b:
            self.canvas.create_oval(x1 - 15, y1 - 43, x1 + 15, y1 - 13,
                                    outline=color, width=width)
            self.canvas.create_text(x1 + 25, y1 - 35, text=f"e{index + 1}", fill=color)
            return

        # Разводим параллельные рёбра визуально.
        peers = []
        for j, (u, v) in enumerate(self.edges):
            if j == index:
                continue
            if self.directed:
                same_pair = (u, v) == (a, b)
            else:
                same_pair = {u, v} == {a, b}
            if same_pair:
                peers.append(j)
        offset = (len(peers) / 2) * 8
        dx, dy = x2 - x1, y2 - y1
        length = max((dx * dx + dy * dy) ** 0.5, 1)
        px, py = -dy / length, dx / length
        # Для каждого параллельного ребра свой сдвиг.
        parallel_indices = [
            j for j, (u, v) in enumerate(self.edges)
            if (u, v) == (a, b) if self.directed
        ] if self.directed else [
            j for j, (u, v) in enumerate(self.edges) if {u, v} == {a, b}
        ]
        rank = parallel_indices.index(index) if index in parallel_indices else 0
        shift = (rank - (len(parallel_indices) - 1) / 2) * 9
        sx, sy = x1 + px * shift, y1 + py * shift
        ex, ey = x2 + px * shift, y2 + py * shift
        vx, vy = ex - sx, ey - sy
        line_len = max((vx * vx + vy * vy) ** 0.5, 1)
        sx += vx / line_len * R
        sy += vy / line_len * R
        ex -= vx / line_len * R
        ey -= vy / line_len * R

        if self.directed:
            self.canvas.create_line(sx, sy, ex, ey, fill=color, width=width,
                                    arrow=tk.LAST, arrowshape=(10, 12, 5))
        else:
            self.canvas.create_line(sx, sy, ex, ey, fill=color, width=width)

        mx, my = (sx + ex) / 2, (sy + ey) / 2
        self.canvas.create_text(mx + px * 10, my + py * 10,
                                text=f"e{index + 1}", fill=color,
                                font=("Arial", 9, "bold"))

    def redraw(self):
        self.canvas.delete("all")
        for i, (a, b) in enumerate(self.edges):
            if a in self.vertices and b in self.vertices:
                self.draw_edge(i, a, b)
        for v, (x, y) in self.vertices.items():
            color = "orange" if v == self.selected else "lightblue"
            self.canvas.create_oval(x - R, y - R, x + R, y + R,
                                    fill=color, outline="black", width=2)
            self.canvas.create_text(x, y, text=str(v), font=("Arial", 10, "bold"))
            if self.directed:
                din, dout = self.directed_degrees(v)
                label = f"−{din} / +{dout}"
            else:
                label = f"d={self.undirected_degree(v)}"
            self.canvas.create_text(x, y + R + 12, text=label, fill="#333333",
                                    font=("Arial", 8))

    # ---------------- Степени и леммы ----------------
    def directed_degrees(self, v):
        # Петля добавляет 1 к входящей и 1 к исходящей степени.
        incoming = sum(1 for a, b in self.edges if b == v)
        outgoing = sum(1 for a, b in self.edges if a == v)
        return incoming, outgoing

    def undirected_degree(self, v):
        degree = 0
        for a, b in self.edges:
            if a == v and b == v:
                degree += 2
            elif a == v or b == v:
                degree += 1
        return degree

    def refresh_degrees(self):
        for item in self.degree_tree.get_children():
            self.degree_tree.delete(item)
        for v in self.vertices:
            if self.directed:
                din, dout = self.directed_degrees(v)
                self.degree_tree.insert("", "end", values=(v, din, dout, din + dout))
            else:
                d = self.undirected_degree(v)
                self.degree_tree.insert("", "end", values=(v, "—", "—", d))

        E = len(self.edges)
        if self.directed:
            sum_in = sum(self.directed_degrees(v)[0] for v in self.vertices)
            sum_out = sum(self.directed_degrees(v)[1] for v in self.vertices)
            odd = sum(1 for v in self.vertices
                      if (sum(self.directed_degrees(v)) % 2) == 1)
            self.lemma_var.set(
                f"Ориентированный граф:\n"
                f"Σdeg− = {sum_in}; |A| = {E}: "
                f"{'выполняется' if sum_in == E else 'нарушено'}\n"
                f"Σdeg+ = {sum_out}; |A| = {E}: "
                f"{'выполняется' if sum_out == E else 'нарушено'}\n"
                f"Вершин с нечётной суммарной степенью: {odd}"
            )
        else:
            degrees = [self.undirected_degree(v) for v in self.vertices]
            total = sum(degrees)
            odd = sum(1 for d in degrees if d % 2)
            self.lemma_var.set(
                f"Неориентированный граф:\n"
                f"Σdeg(v) = {total}; 2|E| = {2 * E}: "
                f"{'выполняется' if total == 2 * E else 'нарушено'}\n"
                f"Вершин нечётной степени: {odd} — "
                f"{'чётно' if odd % 2 == 0 else 'ошибка'}"
            )

    # ---------------- Матрицы ----------------
    def build_adjacency(self):
        ids = list(self.vertices.keys())
        index = {v: i for i, v in enumerate(ids)}
        n = len(ids)
        matrix = [[0 for _ in range(n)] for _ in range(n)]
        cell_edges = {}
        for ei, (u, v) in enumerate(self.edges):
            if u not in index or v not in index:
                continue
            i, j = index[u], index[v]
            matrix[i][j] += 1
            cell_edges.setdefault((i, j), []).append(ei)
            if not self.directed and u != v:
                matrix[j][i] += 1
                cell_edges.setdefault((j, i), []).append(ei)
        return ids, matrix, cell_edges

    def build_incidence(self):
        ids = list(self.vertices.keys())
        matrix = []
        for v in ids:
            row = []
            for a, b in self.edges:
                if self.directed:
                    if a == b == v:
                        value = 0  # у петли +1 и -1 относятся к одной вершине и взаимно компенсируются
                    elif v == a:
                        value = 1
                    elif v == b:
                        value = -1
                    else:
                        value = 0
                else:
                    # Для обычных рёбер 0/1; петля отмечает свою вершину единицей.
                    value = 1 if (v == a or v == b) else 0
                row.append(value)
            matrix.append(row)
        return ids, matrix

    def refresh_matrices(self):
        ids, adj, cell_edges = self.build_adjacency()
        for item in self.adj_tree.get_children():
            self.adj_tree.delete(item)
        columns = [str(v) for v in ids]
        self.adj_tree.configure(columns=columns)
        self.adj_tree.heading("#0", text="V")
        self.adj_tree.column("#0", width=55, minwidth=45, stretch=False, anchor="center")
        for v in ids:
            self.adj_tree.heading(str(v), text=str(v))
            self.adj_tree.column(str(v), width=45, minwidth=35, stretch=False, anchor="center")
        for i, v in enumerate(ids):
            values = [adj[i][j] for j in range(len(ids))]
            self.adj_tree.insert("", "end", iid=f"row_{v}", text=str(v), values=values)

        ids2, inc = self.build_incidence()
        if not ids2:
            content = "(граф пуст)"
        else:
            headers = ["V"] + [f"e{i + 1}" for i in range(len(self.edges))]
            rows = ["\t".join(headers)]
            for i, v in enumerate(ids2):
                rows.append("\t".join([str(v)] + [str(value) for value in inc[i]]))
            content = "\n".join(rows)
        self.inc_text.configure(state="normal")
        self.inc_text.delete("1.0", "end")
        self.inc_text.insert("1.0", content)
        self.inc_text.configure(state="disabled")

    def on_adjacency_cell_click(self, event):
        region = self.adj_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.adj_tree.identify_row(event.y)
        column_id = self.adj_tree.identify_column(event.x)
        if not row_id or column_id == "#0":
            return
        try:
            row_vertex = int(row_id.split("_", 1)[1])
            col_index = int(column_id[1:]) - 1
            ids, matrix, cell_edges = self.build_adjacency()
            col_vertex = ids[col_index]
            row_index = ids.index(row_vertex)
            self.highlight_edges = set(cell_edges.get((row_index, col_index), []))
            self.redraw()
        except (ValueError, IndexError):
            return

    def refresh_all(self):
        self.stats_var.set(
            f"|V| = {len(self.vertices)}/{MAX_VERTICES}    "
            f"|E| = {len(self.edges)}/{MAX_EDGES}\n"
            f"Тип: {'ориентированный' if self.directed else 'неориентированный'}; "
            f"петли/кратные рёбра: {'разрешены' if self.allow_multi_var.get() else 'запрещены'}"
        )
        self.refresh_degrees()
        self.refresh_matrices()

    # ---------------- CSV ----------------
    def copy_csv(self, rows):
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerows(rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(output.getvalue())
        self.root.update()
        messagebox.showinfo("Буфер обмена", "Матрица скопирована в буфер в формате CSV.")

    def copy_adjacency_csv(self):
        ids, matrix, _ = self.build_adjacency()
        rows = [["V"] + ids]
        rows += [[v] + matrix[i] for i, v in enumerate(ids)]
        self.copy_csv(rows)

    def copy_incidence_csv(self):
        ids, matrix = self.build_incidence()
        rows = [["V"] + [f"e{i + 1}" for i in range(len(self.edges))]]
        rows += [[v] + matrix[i] for i, v in enumerate(ids)]
        self.copy_csv(rows)

    # ---------------- JSON ----------------
    def save_json(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить граф", defaultextension=".json",
            filetypes=[("JSON", "*.json")]
        )
        if not path:
            return
        data = {
            "directed": self.directed,
            "allow_loops_and_multiple_edges": self.allow_multi_var.get(),
            "vertices": [
                {"id": v, "x": pos[0], "y": pos[1]}
                for v, pos in self.vertices.items()
            ],
            "edges": [{"u": u, "v": v} for u, v in self.edges],
            "next_vertex_id": self.next_vertex_id,
        }
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            messagebox.showinfo("JSON", "Граф сохранён.")
        except OSError as error:
            messagebox.showerror("Ошибка сохранения", str(error))

    def load_json(self):
        path = filedialog.askopenfilename(
            title="Открыть граф", filetypes=[("JSON", "*.json")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)

            loaded_vertices = {}
            for item in data["vertices"]:
                v = int(item["id"])
                loaded_vertices[v] = (int(item["x"]), int(item["y"]))
            loaded_edges = [(int(item["u"]), int(item["v"])) for item in data["edges"]]

            if len(loaded_vertices) > MAX_VERTICES or len(loaded_edges) > MAX_EDGES:
                raise ValueError("В файле превышены допустимые размеры графа.")
            if any(u not in loaded_vertices or v not in loaded_vertices for u, v in loaded_edges):
                raise ValueError("Некоторые рёбра ссылаются на отсутствующие вершины.")

            self.vertices = loaded_vertices
            self.edges = loaded_edges
            self.directed_var.set(bool(data.get("directed", False)))
            self.allow_multi_var.set(bool(data.get("allow_loops_and_multiple_edges", False)))
            self.next_vertex_id = max(
                int(data.get("next_vertex_id", 1)),
                max(loaded_vertices.keys(), default=0) + 1
            )
            self.selected = None
            self.dragging = None
            self.highlight_edges.clear()
            self.refresh_all()
            self.redraw()
            messagebox.showinfo("JSON", "Граф загружен.")
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            messagebox.showerror("Ошибка загрузки", f"Не удалось загрузить граф:\n{error}")


if __name__ == "__main__":
    root = tk.Tk()
    app = GraphEditor(root)
    root.mainloop()
