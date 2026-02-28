"""Tests for veripy import: Verilog-to-Python converter."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy.import_verilog import import_verilog, import_project, parse, tokenize
from veripy.sim import SimEngine

T = 10

COUNTER_V = """\
module counter #(
    parameter n = 4
) (
    input clock,
    input enable,
    input reset,
    output [3:0] count
);

    reg [3:0] counter;

    assign count = counter;

    always @(posedge clock) begin
        if (reset) begin
            counter <= 0;
        end else
        if (enable) begin
            counter <= (counter + 1);
        end
    end

endmodule
"""

HIER_V = """\
module alu #(
    parameter width = 8
) (
    input [7:0] a,
    input [7:0] b,
    input [3:0] op,
    output reg [7:0] result
);

    always @(*) begin
        case (op)
            0: begin
                result = (a + b);
            end
            1: begin
                result = (a - b);
            end
            default: begin
                result = 0;
            end
        endcase
    end

endmodule

module datapath #(
    parameter width = 8
) (
    input [7:0] a,
    input [7:0] b,
    input clock,
    input [3:0] op,
    input reset,
    output [7:0] result
);

    reg [7:0] piped;
    wire [7:0] alu_a;
    wire [7:0] alu_b;
    wire [3:0] alu_op;
    wire [7:0] alu_result;

    alu #(.width(8)) alu (
        .a(alu_a),
        .b(alu_b),
        .op(alu_op),
        .result(alu_result)
    );

    assign alu_a = a;
    assign alu_b = b;
    assign alu_op = op;

    assign result = piped;

    always @(posedge clock) begin
        if (reset) begin
            piped <= 0;
        end else begin
            piped <= alu_result;
        end
    end

endmodule
"""


class TestTokenizer(unittest.TestCase):
    def test_basic_tokens(self):
        tokens = tokenize('input [3:0] count;')
        kinds = [t[0] for t in tokens]
        self.assertIn('KW', kinds)
        self.assertIn('NUM', kinds)

    def test_nba_token(self):
        tokens = tokenize('counter <= 0;')
        self.assertIn(('LE', '<='), tokens)


class TestParser(unittest.TestCase):
    def test_parse_counter(self):
        modules = parse(COUNTER_V)
        self.assertEqual(len(modules), 1)
        m = modules[0]
        self.assertEqual(m['name'], 'counter')
        self.assertEqual(len(m['params']), 1)
        self.assertEqual(m['params'][0], ('n', 4))

    def test_parse_ports(self):
        m = parse(COUNTER_V)[0]
        port_names = [p['name'] for p in m['ports']]
        self.assertIn('clock', port_names)
        self.assertIn('count', port_names)

    def test_parse_hierarchy(self):
        modules = parse(HIER_V)
        self.assertEqual(len(modules), 2)
        self.assertEqual(modules[0]['name'], 'alu')
        self.assertEqual(modules[1]['name'], 'datapath')

    def test_parse_instance(self):
        dp = parse(HIER_V)[1]
        self.assertEqual(len(dp['instances']), 1)
        inst = dp['instances'][0]
        self.assertEqual(inst['mod_type'], 'alu')
        self.assertEqual(inst['params'], {'width': 8})


class TestCodeGen(unittest.TestCase):
    def test_counter_round_trip(self):
        py = import_verilog(COUNTER_V)
        self.assertIn('class Counter(Module):', py)
        self.assertIn('self.clock = Input()', py)
        self.assertIn('self.count = Output(4)', py)
        self.assertIn('self.counter = Register(4)', py)
        self.assertIn('@self.posedge(self.clock)', py)
        self.assertIn('self.count = self.counter', py)

    def test_counter_executes(self):
        py = import_verilog(COUNTER_V)
        ns = {}
        exec(py, ns)
        Counter = ns['Counter']
        c = Counter(n=4)
        sim = SimEngine(c)
        sim.clock(c.clock, T)
        @sim.initial
        def _():
            c.enable.set(1); c.reset.set(1); yield T; c.reset.set(0)
            for _ in range(5):
                yield T
            self.assertEqual(int(c.count), 5)
        sim.run()

    def test_hierarchy_generates(self):
        py = import_verilog(HIER_V)
        self.assertIn('class Alu(Module):', py)
        self.assertIn('class Datapath(Module):', py)
        self.assertIn('self.alu = Alu(width=8)', py)
        self.assertIn('self.alu.a = self.a', py)
        self.assertIn('self.alu.result', py)

    def test_hierarchy_executes(self):
        py = import_verilog(HIER_V)
        ns = {}
        exec(py, ns)
        Datapath = ns['Datapath']
        d = Datapath(width=8)
        sim = SimEngine(d)
        sim.clock(d.clock, T)
        @sim.initial
        def _():
            d.a.set(10); d.b.set(3); d.op.set(0)
            d.reset.set(1); yield T; d.reset.set(0)
            yield T
            self.assertEqual(int(d.result), 13)
        sim.run()

    def test_instance_wires_not_declared(self):
        py = import_verilog(HIER_V)
        self.assertNotIn('self.alu_a', py)
        self.assertNotIn('self.alu_result', py)

    def test_case_becomes_if_elif(self):
        py = import_verilog(HIER_V)
        self.assertIn('if self.op == 0:', py)
        self.assertIn('elif self.op == 1:', py)


class TestParametricWidth(unittest.TestCase):
    PARAM_V = """\
module alu #(parameter width = 8) (
    input [width-1:0] a,
    input [width-1:0] b,
    output [width-1:0] result
);
    assign result = a + b;
endmodule
"""

    def test_parametric_width_generates(self):
        py = import_verilog(self.PARAM_V)
        self.assertIn('self.a = Input(width)', py)
        self.assertIn('self.result = Output(width)', py)

    def test_parametric_width_executes(self):
        ns = {}
        exec(import_verilog(self.PARAM_V), ns)
        m = ns['Alu'](width=16)
        m.a.set(1000); m.b.set(234)
        m._settle_comb()
        self.assertEqual(int(m.result), 1234)

    def test_parametric_instance_override(self):
        src = self.PARAM_V + """
module top (
    input [15:0] x,
    input [15:0] y,
    output [15:0] z
);
    wire [15:0] alu_z;
    alu #(.width(16)) u0 (
        .a(x), .b(y), .result(alu_z)
    );
    assign z = alu_z;
endmodule
"""
        py = import_verilog(src)
        self.assertIn('self.u0 = Alu(width=16)', py)

    def test_parametric_passthrough_override(self):
        src = self.PARAM_V + """
module wrap #(parameter width = 16) (
    input [width-1:0] x,
    input [width-1:0] y,
    output [width-1:0] z
);
    wire [width-1:0] alu_z;
    alu #(.width(width)) u0 (
        .a(x), .b(y), .result(alu_z)
    );
    assign z = alu_z;
endmodule
"""
        py = import_verilog(src)
        self.assertIn("self.u0 = Alu(width=width)", py)


class TestProjectImport(unittest.TestCase):
    def test_cross_file_imports(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'alu.v'), 'w') as f:
                f.write("""\
module alu (
    input [7:0] a,
    input [7:0] b,
    output [7:0] result
);
    assign result = a + b;
endmodule
""")
            with open(os.path.join(d, 'top.v'), 'w') as f:
                f.write("""\
module top (
    input [7:0] x,
    input [7:0] y,
    output [7:0] z
);
    wire [7:0] alu_z;
    alu u0 (.a(x), .b(y), .result(alu_z));
    assign z = alu_z;
endmodule
""")
            result = import_project(d)
            self.assertIn('alu.py', result)
            self.assertIn('top.py', result)
            self.assertIn('from .alu import Alu', result['top.py'])
            self.assertNotIn('class Alu', result['top.py'])

    def test_cross_file_executes(self):
        import tempfile, os, sys
        with tempfile.TemporaryDirectory() as d:
            pkg = os.path.join(d, 'proj')
            os.makedirs(pkg)
            with open(os.path.join(d, 'alu.v'), 'w') as f:
                f.write("""\
module alu (
    input [7:0] a,
    input [7:0] b,
    output [7:0] result
);
    assign result = a + b;
endmodule
""")
            with open(os.path.join(d, 'top.v'), 'w') as f:
                f.write("""\
module top (
    input [7:0] x,
    input [7:0] y,
    output [7:0] z
);
    wire [7:0] alu_z;
    alu u0 (.a(x), .b(y), .result(alu_z));
    assign z = alu_z;
endmodule
""")
            result = import_project(d)
            for rel, src in result.items():
                with open(os.path.join(pkg, rel), 'w') as f:
                    f.write(src)
            open(os.path.join(pkg, '__init__.py'), 'w').close()
            sys.path.insert(0, d)
            try:
                from proj.top import Top
                t = Top()
                t.x.set(10); t.y.set(20)
                t._settle_comb()
                self.assertEqual(int(t.z), 30)
            finally:
                sys.path.remove(d)
                sys.modules.pop('proj', None)
                sys.modules.pop('proj.top', None)
                sys.modules.pop('proj.alu', None)


if __name__ == '__main__':
    unittest.main()
