"""Tests for veripy import: Verilog-to-Python converter."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import unittest
from veripy.import_verilog import import_verilog, parse, tokenize


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
        c.enable.set(1)
        c.reset.set(1)
        c.tick()
        c.reset.set(0)
        for _ in range(5):
            c.tick()
        self.assertEqual(int(c.count), 5)

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
        d.a.set(10); d.b.set(3); d.op.set(0)
        d.reset.set(1); d.tick(); d.reset.set(0)
        d.tick()
        self.assertEqual(int(d.result), 13)

    def test_instance_wires_not_declared(self):
        py = import_verilog(HIER_V)
        self.assertNotIn('self.alu_a', py)
        self.assertNotIn('self.alu_result', py)

    def test_case_becomes_if_elif(self):
        py = import_verilog(HIER_V)
        self.assertIn('if self.op == 0:', py)
        self.assertIn('elif self.op == 1:', py)


if __name__ == '__main__':
    unittest.main()
