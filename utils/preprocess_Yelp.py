import os
import dgl
import torch
import pandas as pd
import scipy.sparse as sp
from dgl.data import DGLDataset
from torch_geometric.data import HeteroData
from tqdm import tqdm
from dgl.data.utils import save_graphs, load_graphs, generate_mask_tensor, idx2mask
from collections import Counter
from sklearn.model_selection import train_test_split


class YelpDataset(DGLDataset):
    def __init__(self, url=None, raw_dir=None, save_dir=None, force_reload=False, verbose=False):
        self.graph = HeteroData()
        self._ntypes = {"B": "Business", "L": "Location", "S": "Stars", "P": "Phrase"}
        self._relations = [
            ("Business", "Business-Location", "Location"),
            ("Business", "Business-Stars", "Stars"),
            ("Business", "Business-Phrase", "Phrase"),
            ("Phrase", "Phrase-Phrase", "Phrase"),
        ]
        curr_dir = os.path.dirname(__file__)
        parent_dir = os.path.dirname(curr_dir)
        self.data_path = os.path.join(parent_dir, "data/Yelp")
        self.add_reverse_relation()
        super(YelpDataset, self).__init__(
            name="Yelp",
            url=url,
            raw_dir=raw_dir,
            save_dir=save_dir,
            force_reload=force_reload,
            verbose=verbose,
        )

    def add_reverse_relation(self):
        for rel_tuple in self._relations:
            src, rel, dst = rel_tuple
            rev_rel = (dst, f"{dst}-{src}", src)
            if rev_rel not in self._relations:
                self._relations.append(rev_rel)

    def download(self):
        pass

    def load(self):
        print("loading graph")
        graphs, _ = load_graphs(os.path.join(self.data_path, "Yelp_dgl_graph.bin"))
        self.graph = graphs[0]

    def save(self):
        print("saving graph")
        save_graphs(os.path.join(self.data_path, "Yelp_dgl_graph.bin"), [self.graph])

    def process(self):
        chunks = []
        for chunk in pd.read_csv(os.path.join(self.data_path, "node.dat"), sep="\t", names=["node_id", "node_name", "node_type"], chunksize=10000):
            chunks.append(chunk)

        nodes_file = pd.concat(chunks, ignore_index=True)

        nodes = nodes_file["node_id"].tolist()
        nodes_type = nodes_file["node_type"].tolist()

        ## mapping each node id(specific type) into new id by dictionary
        node_dict = {ntype: {"id": [], "feat": []} for ntype in self._ntypes.values()}
        for n, t in zip(nodes, nodes_type):

            ntype = list(self._ntypes.values())[t]

            node_dict[ntype]["id"].append(n)
        for t in node_dict.keys():
            num_ntype = len(node_dict[t]["id"])
            node_dict[t]["feat"] = torch.from_numpy(sp.eye(num_ntype).toarray()).float()
        ## sort node id and feature by node id
        # for ntype in self._ntypes.values():
        #     combined_list = zip(node_dict[ntype]["id"], node_dict[ntype]["feat"])

        #     sorted_combined_list = sorted(combined_list, key=lambda x: x[0])
        #     sort_id = [x[0] for x in sorted_combined_list]
        #     sort_feat = [x[1] for x in sorted_combined_list]

        #     node_dict[ntype]["id"] = sort_id
        #     node_dict[ntype]["feat"] = torch.tensor(sort_feat)

        self.graph = dgl.heterograph(self._read_edges(node_dict))
        self._read_feats_labels(node_dict)

    def _read_edges(self, node_dict):
        edges = {}
        edges_file = pd.read_csv(
            os.path.join(self.data_path, "link.dat"),
            sep="\t",
            names=["u_id", "v_id", "link_type", "link_weight"],
        )

        src = edges_file["u_id"].tolist()
        dst = edges_file["v_id"].tolist()
        etype = edges_file["link_type"].tolist()
        for s, d, t in tqdm(zip(src, dst, etype), total=len(src)):
            rel_tuple = self._relations[int(t)]
            src_t, rel, dst_t = rel_tuple
            s = node_dict[src_t]["id"].index(s)
            d = node_dict[dst_t]["id"].index(d)
            if rel_tuple not in edges:
                edges[rel_tuple] = ([s], [d])
            else:
                edges[rel_tuple][0].append(s)
                edges[rel_tuple][1].append(d)
            if s != t:
                ## self-defined rev relation
                rev_rel = (dst_t, f"{dst_t}-{src_t}", src_t)

                if rev_rel not in edges:
                    edges[rev_rel] = ([d], [s])
                else:
                    edges[rev_rel][0].append(d)
                    edges[rev_rel][1].append(s)

        return edges

    def _read_feats_labels(self, node_dict):
        split_ratio = {"train": 0.8, "val": 0.1, "test": 0.1}
        label_train_file = pd.read_csv(
            os.path.join(self.data_path, "label.dat"),
            sep="\t",
            names=["node_id", "node_name", "node_type", "node_label"],
        )
        label_test_file = pd.read_csv(
            os.path.join(self.data_path, "label.dat.test"),
            sep="\t",
            names=["node_id", "node_name", "node_type", "node_label"],
        )
        label_file = pd.concat([label_train_file, label_test_file], ignore_index=True)
        ## assign node feature
        for ntype in self._ntypes.values():
            self.graph.nodes[ntype].data["feat"] = node_dict[ntype]["feat"]

        ## assign pred node label in graph
        pred_num_nodes = self.graph.num_nodes(self.predict_ntype)
        self.graph.nodes[self.predict_ntype].data["label"] = torch.full((pred_num_nodes, 1), -1)
        label_nodes_indices = []
        for _, row in label_file.iterrows():
            node_id = row["node_id"]
            mapped_node_id = node_dict[self.predict_ntype]["id"].index(node_id)
            # node_type = row["node_type"]
            node_label = row["node_label"]
            self.graph.nodes[self.predict_ntype].data["label"][mapped_node_id] = torch.tensor([node_label])
            label_nodes_indices.append(mapped_node_id)

        ## split label node into train valid test

        ## self-define split ratio
        # num_train_nodes = int(len(label_nodes_indices) * split_ratio["train"])
        # num_valid_nodes = int(len(label_nodes_indices) * split_ratio["val"])
        # eva_indices = {
        #     "train": label_nodes_indices[:num_train_nodes],
        #     "val": label_nodes_indices[num_train_nodes : num_train_nodes + num_valid_nodes],
        #     "test": label_nodes_indices[num_train_nodes + num_valid_nodes :],
        # }

        ## sklearn split data
        train_indices, test_indices = train_test_split(label_nodes_indices, test_size=split_ratio["test"], random_state=42)
        train_indices, val_indices = train_test_split(train_indices, test_size=split_ratio["val"], random_state=42)
        eva_indices = {
            "train": train_indices,
            "val": val_indices,
            "test": test_indices,
        }
        for name, indices in eva_indices.items():
            mask = generate_mask_tensor(idx2mask(indices, pred_num_nodes))
            self.graph.nodes[self.predict_ntype].data[name] = mask

    def has_cache(self):
        return os.path.exists(os.path.join(self.data_path, "Yelp_dgl_graph.bin"))

    def __getitem__(self, i):
        return self.graph

    def __len__(self):
        return 1

    @property
    def num_classes(self):
        return 16

    @property
    def relations(self):
        return self._relations

    @property
    def predict_ntype(self):
        return "Business"

    @property
    def has_label_ratio(self):
        return False

    @property
    def multilabel(self):
        return True
