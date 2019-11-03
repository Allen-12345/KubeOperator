import kubernetes.client
import redis
import json
from kubernetes.client.rest import ApiException
from kubeops_api.cluster_data import ClusterData,Pod,NameSpace,Node
from kubeops_api.models.cluster import Cluster
from django.db.models import Q



class ClusterMonitor():

    def __init__(self, cluster):
        # init redis
        self.redis_cli = redis.StrictRedis(host='localhost', port=6379)
        self.cluster = cluster
        self.retry_count = 0
        self.get_authorization()
        self.get_api_instance()

    def list_pods(self):
        try:
            pods = self.api_instance.list_pod_for_all_namespaces()
            podList = []
            for p in pods.items:
                status =p.status
                pod = Pod(name=p.metadata.name,cluster_name=self.cluster.name,restart_count=0,status=status.phase,name_space=p.metadata.namespace,
                          host_ip=status.host_ip,pod_ip=status.pod_ip,host_name=None)
                podList.append(pod.__dict__)
            return podList
        except ApiException as e:
            raise Exception('list pod failed!' + e.reason)

    def get_authorization(self):
        try:
            if self.redis_cli.exists(self.cluster.name):
                cluster_str = str(self.redis_cli.get(self.cluster.name),encoding= 'utf-8')
                cluster_data = json.loads(cluster_str)
                self.token = cluster_data['token']
            else:
                self.token = self.cluster.get_cluster_token()
        except ApiException as e:
            if e.status == 401:
                self.token = self.cluster.get_cluster_token()
            else:
                raise Exception('get authorization failed!' + e.reason)

    def get_api_instance(self):
        self.cluster.change_to()
        master = self.cluster.group_set.get(name='master').hosts.first()
        configuration = kubernetes.client.Configuration()
        configuration.api_key_prefix['authorization'] = 'Bearer'
        configuration.api_key['authorization'] = self.token
        configuration.debug = True
        configuration.host = 'https://' + master.ip + ":6443"
        configuration.verify_ssl = False
        self.api_instance = kubernetes.client.CoreV1Api(kubernetes.client.ApiClient(configuration))
        self.check_authorization(self.retry_count)

    def check_authorization(self,retry_count):
        if retry_count > 2:
            raise Exception('init k8s client failed! retry_count=' + str(retry_count))
        self.retry_count = retry_count + 1
        try:
            self.api_instance.list_node()
        except ApiException as e:
            if e.status == 401:
                self.get_authorization()
                self.get_api_instance()
            else:
                raise Exception('init k8s client failed!' + e.reason)

    def list_name_spaces(self):
        name_spaces = self.api_instance.list_namespace()
        name_space_list = []
        for n in name_spaces.items:
            name_space = NameSpace(name=n.metadata.name,status=n.status.phase)
            name_space_list.append(name_space.__dict__)
        return name_space_list

    def list_nodes(self):
        nodes = self.api_instance.list_node()
        node_list = []
        for n in nodes.items:
            node = Node(name=n.metadata.name,status=n.status.phase)
            node_list.append(node.__dict__)
        return node_list

    def set_cluster_data(self):
        nodes = self.list_nodes()
        pods = self.list_pods()
        name_spaces = self.list_name_spaces()
        cluster_data = ClusterData(cluster=self.cluster,token=self.token,pods=pods,nodes=nodes,name_spaces=name_spaces)
        return self.redis_cli.set(self.cluster.name,json.dumps(cluster_data.__dict__))

    def list_cluster_data(self):
        clusters = Cluster.objects.filter(~Q(status=Cluster.CLUSTER_STATUS_READY))
        cluster_data_list = []
        for c in clusters:
            cluster_str = str(self.redis_cli.get(c.name),encoding= 'utf-8')
            if cluster_str is not None:
                cluster_d = json.loads(cluster_str)
                cluster_data_list.append(cluster_d)
        return cluster_data_list
