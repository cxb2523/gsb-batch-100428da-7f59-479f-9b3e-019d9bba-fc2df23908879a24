import {beforeEach, describe, expect, it, vi} from "vitest";

// 这些模块在浏览器/tauri 环境下才可用，测试里替换成空实现
vi.mock("@tauri-apps/plugin-dialog", () => ({open: vi.fn()}));
vi.mock("@tauri-apps/plugin-fs", () => ({
    BaseDirectory: {},
    exists: vi.fn(async () => true),
    writeFile: vi.fn(),
    create: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-sql", () => ({default: {load: vi.fn()}}));
vi.mock("@/utils/api", () => ({api: {post: vi.fn(), get: vi.fn()}}));
vi.mock("@antv/x6-plugin-selection", () => ({Selection: class {}}));
vi.mock("@antv/x6-plugin-keyboard", () => ({Keyboard: class {}}));
vi.mock("element-plus", () => ({ElMessage: {success: vi.fn()}}));
vi.mock("@/components/api-flow/engine/state-sync", () => ({
    StateSync: class {
        addSocket() {
        }
    },
}));

import {FlowEditor, FlowRunner, FlowStore} from "@/components/api-flow/engine/editor";
import type {Edge, Node} from "@/components/api-flow/engine/types";

function makeNode(id: string): Node {
    return {
        id,
        name: id,
        key: id,
        input: [],
        output: [],
        func: {lang: "javascript", code: ""},
    };
}

// 构造一个只依赖本地同步函数的样例流程，节点行为通过 FineNode.runNode 覆盖
function buildFlow(nodes: { id: string; run: () => any }[], edges: [string, string][]) {
    const store = new FlowStore();
    nodes.forEach(({id, run}) => {
        const fineNode = store.addNode(makeNode(id));
        //与 FineNode.runNode 一致：返回输出参数的 key 列表（或输出对象）
        fineNode.runNode = async () => {
            const res = await run();
            if (res === undefined) {
                return [];
            }
            return Array.isArray(res) ? res : Object.keys(res);
        };
    });
    edges.forEach(([source, target]) => {
        const edge: Edge = {
            id: `${source}-out-NODE_OUT__${target}-in-NODE_IN`,
            source: {node: source, port: "NODE_OUT"},
            target: {node: target, port: "NODE_IN"},
        };
        store.addEdge(edge);
    });
    return store;
}

const nodeState = (store: FlowStore, id: string) => store.fineNodeMap[id].states.nodeState.runState;

describe("FlowRunner 运行结局", () => {
    let store: FlowStore;
    let runner: FlowRunner;

    beforeEach(() => {
        store = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
                {id: "C", run: () => ({})},
            ],
            [["A", "B"], ["B", "C"]]
        );
        runner = new FlowRunner(store);
    });

    it("正常链路：结局为成功，所有节点成功", async () => {
        const result = await runner.run(() => {});

        expect(result.status).toBe("success");
        expect(result.successNodeIds).toEqual(["A", "B", "C"]);
        expect(result.notExecutedNodeIds).toEqual([]);
        expect(result.nodeStates).toEqual({A: "success", B: "success", C: "success"});
        expect(result.errorNodeId).toBeNull();
        expect(result.errors).toEqual([]);
        ["A", "B", "C"].forEach((id) => expect(nodeState(store, id)).toBe("success"));
        expect(store.flowRunning.value).toBe(false);
    });

    it("中游节点抛错：结局为失败，保留节点与错误信息，下游不触发", async () => {
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("boom");
        };
        const downstream = vi.fn();
        store.fineNodeMap["C"].runNode = async () => {
            downstream();
            return [];
        };

        const result = await runner.run(() => {});

        expect(result.status).toBe("failed");
        expect(result.errorNodeId).toBe("B");
        expect(result.errorMessage).toContain("boom");
        expect(result.successNodeIds).toEqual(["A"]);
        expect(result.notExecutedNodeIds).toEqual(["C"]);
        expect(result.nodeStates).toEqual({A: "success", B: "error", C: "none"});
        expect(nodeState(store, "A")).toBe("success");
        expect(nodeState(store, "B")).toBe("error");
        expect(nodeState(store, "C")).toBe("none");
        expect(downstream).not.toHaveBeenCalled();
    });

    it("运行中手动停止：结局为已停止，跑完的节点保持成功，没跑到的是未执行", async () => {
        //模拟真实 FineNode.runNode：执行期间轮询 flowRunning，被停止时中断
        store.fineNodeMap["A"].runNode = () => new Promise<string[]>((resolve, reject) => {
            const it = setInterval(() => {
                if (!store.flowRunning.value) {
                    clearInterval(it);
                    reject(new Error("premature termination of process"));
                }
            }, 200);
        });

        const running = runner.run(() => {});
        await new Promise((resolve) => setTimeout(resolve, 50));
        await runner.stop();
        const result = await running;

        expect(result.status).toBe("stopped");
        expect(result.errorNodeId).toBeNull();
        expect(result.errorMessage).toBeNull();
        expect(result.errors).toEqual([]);
        expect(result.notExecutedNodeIds).toEqual(expect.arrayContaining(["A", "B", "C"]));
        expect(result.successNodeIds).toEqual([]);
        ["A", "B", "C"].forEach((id) => expect(nodeState(store, id)).toBe("none"));
        expect(store.flowRunning.value).toBe(false);
    });

    it("停止前已跑完的节点保持成功态，其余为未执行", async () => {
        //A 立即完成，B 卡住直到被停止
        store.fineNodeMap["B"].runNode = () => new Promise<string[]>((resolve, reject) => {
            const it = setInterval(() => {
                if (!store.flowRunning.value) {
                    clearInterval(it);
                    reject(new Error("premature termination of process"));
                }
            }, 200);
        });

        const running = runner.run(() => {});
        await new Promise((resolve) => setTimeout(resolve, 100));
        await runner.stop();
        const result = await running;

        expect(result.status).toBe("stopped");
        expect(result.successNodeIds).toEqual(["A"]);
        expect(result.notExecutedNodeIds).toEqual(expect.arrayContaining(["B", "C"]));
        expect(nodeState(store, "A")).toBe("success");
        expect(nodeState(store, "B")).toBe("none");
    });

    it("连续两次运行：第二次结局不继承第一次", async () => {
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("first-run-error");
        };
        const first = await runner.run(() => {});
        expect(first.status).toBe("failed");

        store.fineNodeMap["B"].runNode = async () => [];
        const second = await runner.run(() => {});

        expect(second.status).toBe("success");
        expect(second.errorNodeId).toBeNull();
        expect(second.errorMessage).toBeNull();
        expect(second.errors).toEqual([]);
        expect(second.successNodeIds).toEqual(["A", "B", "C"]);
        expect(nodeState(store, "B")).toBe("success");
        expect(store.fineNodeMap["B"].states.nodeState.error.message).toBe("");
    });

    it("runStartAtNode：从中间节点开始也能取到结局，未参与的节点为未执行", async () => {
        const result = await runner.runStartAtNode("B", () => {});

        expect(result.status).toBe("success");
        expect(result.successNodeIds).toEqual(["B", "C"]);
        expect(result.notExecutedNodeIds).toEqual(["A"]);
        expect(nodeState(store, "A")).toBe("none");
    });

    it("runNodeSelf：只执行自身，结局与节点状态一致", async () => {
        const result = await runner.runNodeSelf("B", () => {});

        expect(result.status).toBe("success");
        expect(result.successNodeIds).toEqual(["B"]);
        expect(result.notExecutedNodeIds).toEqual(["A", "C"]);
    });

    it("runNodeSelf 节点抛错时结局为失败且出错节点为错误态", async () => {
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("self-error");
        };
        const result = await runner.runNodeSelf("B", () => {});

        expect(result.status).toBe("failed");
        expect(result.errorNodeId).toBe("B");
        expect(result.errorMessage).toContain("self-error");
        expect(nodeState(store, "B")).toBe("error");
    });

    it("onlySync 与禁用连线不参与触发（保持原语义）", async () => {
        //A->B 是普通触发线；A->C 为仅同步，A->D 为禁用，这两条线不应触发 C、D
        const extra = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
                {id: "C", run: () => ({})},
                {id: "D", run: () => ({})},
            ],
            [["A", "B"], ["A", "C"], ["A", "D"]]
        );
        extra.edgeMap["A-out-NODE_OUT__C-in-NODE_IN"].states = {mode: "onlySync"};
        extra.edgeMap["A-out-NODE_OUT__D-in-NODE_IN"].states = {disable: true};
        const extraRunner = new FlowRunner(extra);

        const result = await extraRunner.run(() => {});

        expect(result.status).toBe("success");
        expect(result.successNodeIds).toEqual(["A", "B"]);
        expect(result.notExecutedNodeIds).toEqual(["C", "D"]);
        expect(extra.fineNodeMap["C"].states.nodeState.runState).toBe("none");
        expect(extra.fineNodeMap["D"].states.nodeState.runState).toBe("none");
    });
});

// 三个现有运行入口都经由 FlowEditor，这里用原型对象直接验证入口与结局的对接
function makeEditor(store: FlowStore) {    const editor = Object.create(FlowEditor.prototype) as any;
    editor.store = store;
    editor.runner = new FlowRunner(store);
    editor.render = {
        updateNode: () => {
        },
        clearEdgesState: () => {
        },
        setEdgeState: () => {
        },
    };
    return editor as FlowEditor;
}

describe("FlowEditor 运行入口与结局", () => {
    it("runFlowBySignal / runStartAtNode / runNodeSelf 都能取到本次结局", async () => {
        const store = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
                {id: "C", run: () => ({})},
            ],
            [["A", "B"], ["B", "C"]]
        );
        const editor = makeEditor(store);

        const whole = await editor.runFlowBySignal(() => {});
        expect(whole.status).toBe("success");
        expect(whole.nodeStates).toEqual({A: "success", B: "success", C: "success"});

        const fromB = await editor.runStartAtNode("B", () => {});
        expect(fromB.status).toBe("success");
        expect(fromB.successNodeIds).toEqual(["B", "C"]);
        expect(editor.flowRunResult.status).toBe("success");

        const self = await editor.runNodeSelf("A", () => {});
        expect(self.status).toBe("success");
        expect(self.successNodeIds).toEqual(["A"]);
        expect(self.notExecutedNodeIds).toEqual(["B", "C"]);
    });

    it("流程失败时通过入口取到的结局带出错节点，且该节点为错误态", async () => {
        const store = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
            ],
            [["A", "B"]]
        );
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("entry-error");
        };
        const editor = makeEditor(store);

        const result = await editor.runFlowBySignal(() => {});

        expect(result.status).toBe("failed");
        expect(result.errorNodeId).toBe("B");
        expect(result.nodeStates["B"]).toBe("error");
        expect(store.fineNodeMap["B"].states.nodeState.runState).toBe("error");
        expect(editor.store.flowRunning.value).toBe(false);
    });

    it("下一次运行开始时重置上一轮结局", async () => {
        const store = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
            ],
            [["A", "B"]]
        );
        const editor = makeEditor(store);
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("first-error");
        };
        const first = await editor.runFlowBySignal(() => {});
        expect(first.status).toBe("failed");

        store.fineNodeMap["B"].runNode = async () => [];
        const second = await editor.runFlowBySignal(() => {});

        expect(second.status).toBe("success");
        expect(second.errors).toEqual([]);
        expect(second.errorNodeId).toBeNull();
        expect(editor.flowRunResult.errorNodeId).toBeNull();
        expect(store.fineNodeMap["B"].states.nodeState.error.message).toBe("");
    });
});

// 不 mock runNode，走真实的 JS 函数执行链（eval 节点 func.code）
function buildJsFlow(nodes: { id: string; code: string }[], edges: [string, string][]) {
    const store = new FlowStore();
    nodes.forEach(({id, code}) => {
        store.addNode({
            id,
            name: id,
            key: id,
            input: [],
            output: [],
            func: {lang: "javascript", code},
        });
    });
    edges.forEach(([source, target]) => {
        store.addEdge({
            id: `${source}-out-NODE_OUT__${target}-in-NODE_IN`,
            source: {node: source, port: "NODE_OUT"},
            target: {node: target, port: "NODE_IN"},
        });
    });
    return store;
}

describe("FlowRunner 真实节点函数执行", () => {
    it("正常链路：真实的 JS 节点函数全部执行成功", async () => {
        const store = buildJsFlow(
            [
                {id: "A", code: "async () => ({ out: 1 })"},
                {id: "B", code: "async (inputs) => ({ out: inputs.NODE_IN ? 2 : 3 })"},
            ],
            [["A", "B"]]
        );
        const runner = new FlowRunner(store);

        const result = await runner.run(() => {});

        expect(result.status).toBe("success");
        expect(result.successNodeIds).toEqual(["A", "B"]);
        expect(result.notExecutedNodeIds).toEqual([]);
    });

    it("真实节点函数抛错：结局为失败，下游保持未执行", async () => {
        const store = buildJsFlow(
            [
                {id: "A", code: "async () => ({ out: 1 })"},
                {id: "B", code: "async () => { throw new Error('mid-node-broken') }"},
                {id: "C", code: "async () => ({ out: 3 })"},
            ],
            [["A", "B"], ["B", "C"]]
        );
        const runner = new FlowRunner(store);

        const result = await runner.run(() => {});

        expect(result.status).toBe("failed");
        expect(result.errorNodeId).toBe("B");
        expect(result.errorMessage).toContain("mid-node-broken");
        expect(result.successNodeIds).toEqual(["A"]);
        expect(result.notExecutedNodeIds).toEqual(["C"]);
        expect(store.fineNodeMap["B"].states.nodeState.runState).toBe("error");
        expect(store.fineNodeMap["C"].states.nodeState.runState).toBe("none");
    });
});

describe("FlowRunner 失败与停止并存时", () => {
    it("已有节点真实抛错时结局判失败，并保留出错节点", async () => {
        const store = buildFlow(
            [
                {id: "A", run: () => ({})},
                {id: "B", run: () => ({})},
                {id: "C", run: () => ({})},
            ],
            [["A", "B"], ["A", "C"]]
        );
        store.fineNodeMap["B"].runNode = async () => {
            throw new Error("branch-broken");
        };
        //C 分支一直卡住，等用户停止
        store.fineNodeMap["C"].runNode = () => new Promise<string[]>((resolve, reject) => {
            const it = setInterval(() => {
                if (!store.flowRunning.value) {
                    clearInterval(it);
                    reject(new Error("premature termination of process"));
                }
            }, 200);
        });
        const runner = new FlowRunner(store);

        const running = runner.run(() => {});
        await new Promise((resolve) => setTimeout(resolve, 120));
        await runner.stop();
        const result = await running;

        expect(result.status).toBe("failed");
        expect(result.errorNodeId).toBe("B");
        expect(store.fineNodeMap["B"].states.nodeState.runState).toBe("error");
        expect(result.notExecutedNodeIds).toEqual(["C"]);
        expect(store.fineNodeMap["C"].states.nodeState.runState).toBe("none");
    });
});
