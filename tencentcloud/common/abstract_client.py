# -*- coding: utf-8 -*-
#
# Copyright 1999-2017 Tencent Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
from datetime import datetime
import hashlib
import json
import random
import sys
import time
import warnings

try:
    from urllib.parse import urlencode
except ImportError:
    from urllib import urlencode

import tencentcloud
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.common.http.request import ApiRequest
from tencentcloud.common.http.request import RequestInternal
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.sign import Sign

warnings.filterwarnings("ignore")

_json_content = 'application/json'
_multipart_content = 'multipart/form-data'
_form_urlencoded_content = 'application/x-www-form-urlencoded'

class AbstractClient(object):
    _requestPath = '/'
    _params = {}
    _apiVersion = ''
    _endpoint = ''
    _sdkVersion = 'SDK_PYTHON_%s' % tencentcloud.__version__
    _default_content_type = _form_urlencoded_content

    def __init__(self, credential, region, profile=None):
        if credential is None:
            raise TencentCloudSDKException(
                "InvalidCredential", "Credential is None or invalid")
        self.credential = credential
        self.region = region
        self.profile = ClientProfile() if profile is None else profile

        # self.secretId = self.credential.secretId
        # self.secretKey = self.credential.secretKey
        # self.defaultRegion = self.region or ''
        # self.reqMethod = self.profile.httpProfile.reqMethod
        # self.signMethod = self.profile.signMethod
        # self.requestHost = '.'.join((self._service_name, self.profile.httpProfile.endpoint))
        # self.apiRequest = ApiRequest(self.requestHost, req_timeout=self.profile.httpProfile.reqTimeout)
        # self.token = self.credential.token or ''

    def _build_header(self, req):
        # 目前不需要支持Keep-Alive特性
        # if self.apiRequest.is_keep_alive():
        #    req.header["Connection"] = "Keep-Alive"
        if req.method == 'POST':
            req.header["Content-Type"] = "application/x-www-form-urlencoded"

    def _fix_params(self, params):
        if not isinstance(params, (dict,)):
            return params
        return self._format_params(None, params)

    def _format_params(self, prefix, params):
        d = {}
        if params is None:
            return d

        if not isinstance(params, (tuple, list, dict)):
            d[prefix] = params
            return d

        if isinstance(params, (list, tuple)):
            for idx, item in enumerate(params):
                if prefix:
                    key = "{0}.{1}".format(prefix, idx)
                else:
                    key = "{0}".format(idx)
                d.update(self._format_params(key, item))
            return d

        if isinstance(params, dict):
            for k, v in params.items():
                if prefix:
                    key = '{0}.{1}'.format(prefix, k)
                else:
                    key = '{0}'.format(k)
                d.update(self._format_params(key, v))
            return d

        raise TencentCloudSDKException("ClientParamsError", "some params type error")

    def _build_req_inter(self, action, params, req_inter):
        if self.profile.signMethod in ("HmacSHA1", "HmacSHA256"):
            self._build_req_with_old_signature(action, params, req_inter)
        elif self.profile.signMethod == "TC3-HMAC-SHA256":
            self._build_req_with_tc3_signature(action, params, req_inter)
        else:
            raise TencentCloudSDKException("ClientError", "Invalid signature method.")

    def _build_req_with_old_signature(self, action, params, req):
        params = copy.deepcopy(self._fix_params(params))
        params['Action'] = action[0].upper() + action[1:]
        params['RequestClient'] = self._sdkVersion
        params['Nonce'] = random.randint(1, sys.maxsize)
        params['Timestamp'] = int(time.time())
        params['Version'] = self._apiVersion

        if self.region:
            params['Region'] = self.region

        if self.credential.token:
            params['Token'] = self.credential.token

        if self.credential.secretId:
            params['SecretId'] = self.credential.secretId

        if self.profile.signMethod:
            params['SignatureMethod'] = self.profile.signMethod

        signInParam = self._format_sign_string(params)
        params['Signature'] = Sign.sign(str(self.credential.secretKey),
                                        str(signInParam),
                                        str(self.profile.signMethod))

        req.data = urlencode(params)
        self._build_header(req)

    def _build_req_with_tc3_signature(self, action, params, req):
        content_type = self._default_content_type
        if req.method == 'GET':
            contentT_type = _form_urlencoded_content
        elif req.method == 'POST':
            content_type = 'application/json'

        endpoint = self._get_endpoint()
        service = endpoint.split('.')[0]
        timestamp = int(time.time())
        date = datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d')

        req.header["Content-Type"] = content_type
        req.header["Host"] = endpoint
        req.header["X-TC-Action"] = action[0].upper() + action[1:]
        req.header["X-TC-RequestClient"] = self._sdkVersion
        req.header["X-TC-Nonce"] = random.randint(1, sys.maxsize)
        req.header["X-TC-Timestamp"] = timestamp
        req.header["X-TC-Version"] = self._apiVersion
        if self.profile.unsignedPayload is True:
            req.header["X-TC-Content-SHA256"] = "UNSIGNED-PAYLOAD"
        if self.region:
            req.header['X-TC-Region'] = self.region
        if self.credential.token:
            req.header['X-TC-Token'] = self.credential.token

        signature = self._get_tc3_signature(params, req, date, service)

        auth = "TC3-HMAC-SHA256"
        auth += " Credential=%s/%s/%s/tc3_request" % (self.credential.secretId, date, service)
        auth += ", SignedHeaders=content-type;host, Signature=%s" % signature
        req.header["Authorization"] = auth

    def _get_tc3_signature(self, params, req, date, service):
        canonical_uri = req.uri
        canonical_querystring = ''

        if req.method == 'GET':
            params = copy.deepcopy(self._fix_params(params))
            req.data = urlencode(params)
            canonical_querystring = req.data
            payload_hash = hashlib.sha256(''.encode('utf-8')).hexdigest()
        else:
            ct = req.header["Content-Type"]
            if ct == _json_content:
                req.data = json.dumps(params)
            elif ct == _multipart_content:
                boundary = self._gen_bounary()
                req.header["Content-Type"] = ct + "; boundary=" + boundary
                req.data = self._get_multipart_body(params, boundary)
            else:
                raise Exception("Unsupported content type: %s" % ct)
            payload_hash = hashlib.sha256(req.data.encode('utf-8')).hexdigest()
        if req.header.get("X-TC-Content-SHA256") == "UNSIGNED-PAYLOAD":
            payload_hash = hashlib.sha256("UNSIGNED-PAYLOAD".encode('utf-8')).hexdigest()

        canonical_headers = 'content-type:%s\nhost:%s\n' % (
            req.header["Content-Type"], req.header["Host"])
        signed_headers = 'content-type;host'
        canonical_request = '%s\n%s\n%s\n%s\n%s\n%s' % (req.method,
                                                        canonical_uri,
                                                        canonical_querystring,
                                                        canonical_headers,
                                                        signed_headers,
                                                        payload_hash)

        algorithm = 'TC3-HMAC-SHA256'
        credential_scope = date + '/' + service + '/tc3_request'
        digest = hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
        string2sign = '%s\n%s\n%s\n%s' % (algorithm,
                                          req.header["X-TC-Timestamp"],
                                          credential_scope,
                                          digest)

        signature = Sign.sign_tc3(self.credential.secretKey, date, service, string2sign)
        return signature

    def _get_multipart_body(self, params, boundary):
        body = ''
        for k, v in params.items():
            body += '--%s\r\n' % boundary
            body += 'Content-Disposition: form-data; name="%s"\r\n' % k
            if isinstance(v, list) or isinstance(v, dict):
                v = json.dumps(v)
                body += 'Content-Type: application/json\r\n'
            body += '\r\n%s\r\n' % v
        if body != '':
            body += '--%s--\r\n' % boundary
        return body

    def _check_status(self, resp_inter):
        if resp_inter.status != 200:
            raise TencentCloudSDKException("ServerNetworkError", resp_inter.data)

    def _format_sign_string(self, params):
        formatParam = {}
        for k in params:
            formatParam[k.replace('_', '.')] = params[k]
        strParam = '&'.join('%s=%s' % (k, formatParam[k]) for k in sorted(formatParam))
        msg = '%s%s%s?%s' % (self.profile.httpProfile.reqMethod, self._get_endpoint(), self._requestPath, strParam)
        return msg

    def _get_endpoint(self):
        endpoint = self.profile.httpProfile.endpoint
        if endpoint is None:
            endpoint = self._endpoint
        return endpoint

    def call(self, action, params):
        endpoint = self._get_endpoint()

        req_inter = RequestInternal(endpoint, self.profile.httpProfile.reqMethod, self._requestPath)
        self._build_req_inter(action, params, req_inter)

        apiRequest = ApiRequest(self._get_endpoint(), self.profile.httpProfile.reqTimeout)

        resp_inter = apiRequest.send_request(req_inter)
        self._check_status(resp_inter)
        data = resp_inter.data
        if sys.version_info[0] > 2:
            data = data.decode()
        else:
            data = data.decode(encoding='UTF-8')
        return data
