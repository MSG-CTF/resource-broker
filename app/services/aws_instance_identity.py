from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class InvalidAwsInstanceIdentityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AwsInstanceIdentity:
    account_id: str
    instance_id: str
    region: str


_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_INSTANCE_ID = re.compile(r"^i-(?:[0-9a-f]{8}|[0-9a-f]{17})$")
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+$")

# Source: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/regions-certs.html
# AWS publishes the RSA certificates used to sign EC2 instance identity
# documents.  Only the public keys are needed for the documented SHA-256/RSA
# verification procedure.  Regions sharing a key are grouped to avoid
# duplicating certificate material.
_PUBLIC_KEY_GROUPS: tuple[tuple[frozenset[str], bytes], ...] = (
    (
        frozenset(
            {
                "us-east-1", "us-east-2", "us-west-1", "us-west-2",
                "ap-south-1", "ap-northeast-3", "ap-northeast-2",
                "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
                "ca-central-1", "eu-central-1", "eu-west-1", "eu-west-2",
                "eu-west-3", "eu-north-1", "sa-east-1",
            }
        ),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCHvRjf/0kStpJ248khtIaN8qkD
N3tkw4VjvA9nvPl2anJO+eIBUqPfQG09kZlwpWpmyO8bGB2RWqWxCwuB/dcnIob6
w420k9WY5C0IIGtDRNauN3kuvGXkw3HEnF0EjYr0pcyWUvByWY4KswZV42X7Y7XS
S13hOIcL6NLA+H94/QIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"cn-north-1", "cn-northwest-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC6GFQ2WoBl1xZYH85INUMaTc4D
30QXM6f+YmWZyJD9fC7Z0UlaZIKoQATqCO58KNCre+jECELYIX56Uq0lb8LRLP8t
ijrQ9Sp3qJcXiH66kH0eQ44a5YdewcFOy+CSAYDUIaB6XhTQJ2r7bd4A2vw3ybbx
TOWONKdOWtgIe3M3iwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"us-gov-east-1", "us-gov-west-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCpohwYUVPH9I7Vbkb3WMe/JB0Y
/bmfVj3VpcK445YBRO9K80alesjgBc2tAX4KYg4Lht4EBKccLHTzaNi51YEGX1aL
NrSmxhz1+WtzNLNUsyY3zD9zvwX/3k1+JB2dRA+m+Cpwx4mjzZyAeQtHtegVaAyt
kmqtxQrSCexBxvqRqQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"af-south-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDFd571nUzVtke3rPyRkYfvs3jh
0C0EMzzG72boyUNjnfw1+m0TeFraTLKb9T6F7TuB/ZEN+vmlYqr2+5Va8U8qLbPF
0bRH+FdaKjhgWZdYXxGzQzU3ioy5W5ZM1VyB7iUsxEAlxsybC3ziPYaHI42UiTkQ
NahmoroNeqVyHNnBpQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-east-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC1kkHXYTfc7gY5Q55JJhjTieHA
gacaQkiRPity9QPDE3b+NXDh4UdP1xdIw73JcIIG3sG9RhWiXVCHh6KkuCTqJfPU
knIKk8vsM3RXflUpBe8Pf+P92pxqPMCz1Fr2NehS3JhhpkCZVGxxwLC5gaG0Lr4r
FORubjYYRh84dK98VwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-east-2"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDVdclgpcIIsyGG51ag8srK1UMf
Z+Ioh9E4PLP+cIWQcdh31fv2kV/whKaqfmifNefBIuHrTPl6L+mJQbsfeA+TiEx1
Jamg4lwQfhgQKQCmjZVMaHtK+AY57TVN6FBfHXxT2MXzktsKxAQRGeSTFQYrHRzS
Yvu+GkA+37lqyWRfcwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-south-2"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDTwHu0ND+sFcobrjvcAYm0PNRD
8f4R1jAzvoLt2+qGeOTAyO1Httj6cmsYN3AP1hN5iYuppFiYsl2eNPa/CD0Vg0BA
fDFlV5rzjpA0j7TJabVh4kj7JvtD+xYMi6wEQA4x6SPONY4OeZ2+8o/HS8nucpWD
VdPRO6ciWUlMhjmDmwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-southeast-3"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCnCS/Vbt0gQ1ebWcur2hSO7PnJ
ifE4OPxQ7RgSAlc4/spJp1sDP+ZrS0LO1ZJfKhXf1R9S3AUwLnsc7b+IuVXdY5LK
9RKqu64nyXP5dx170zoL8loEyCSuRR2fs+04i2QsWBVP+KFNAn7P5L1EHRjkgTO8
kjNKviwRV+OkP9ab5wIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-southeast-4"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDHezwQr2VQpQSTW5TXNefiQrP+
qWTGAbGsPeMX4hBMjAJUKys2NIRcRZaLM/BCew2FIPVjNtlaj6Gwn9ipU4Mlz3zI
wAMWi1AvGMSreppt+wV6MRtfOjh0Dvj/veJe88aEZJMozNgkJFRS+WFWsckQeL56
tf6kY6QTlNo8V/0CsQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-southeast-5"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDFuKydxZsordNH7bLwIluEGOkX
7/CdLdpeqkDKEhQkFwzpRxaX4EAlkGh2/o7D8qneC9cGQhqSG5WVVBrmZG7sfkFO
M4m1AtY++kfv+MYto1VFgLk1xJbkpq1r4YeQUl+ZsJYsZpyX/t+g8s7rW0OVcBsY
x4L75bf34z38mwK8PQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-southeast-6"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC9AsBNOUfT8QpguHqMa9KIXP/Y
gZOmfRAgEmnLawXTC+40bXUq/fwkx+cebv3xCvUx6d07/iDlD0dm2Hf2HhPFi3Oa
CI7vc8c3lqVBvcn6yzmwv35aSEvtVm2NHC3j4/q6vN2tR2d4a1Mf1QhmOVOJ7XiT
EbCy2Yv+pmDbk29V6QIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ap-southeast-7"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCajgAeOauwvqGDLrHvxujnZ1Bn
kMzwjrycMUTkj8jqNtWoDQWUJVNPZJILosEUVwK2I3oNkEsx/ryl9XfXcNNceoYf
VEPzkTzozrZyuOG66FWtUU1LKeJ7h9/rX0Zd9lZEokrdr6dLPt9FsHWaK5ExlUnW
BjNltcQLkkKqoeYaFwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"ca-west-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDK1kIcG5Q6adBXQM75GldfTSiX
l7tn54p10TnspI0ErDdb2B6q2Ji/v4XBVH13ZCMgqlRHMqV8AWI5iO6gFn2A9sN3
AZXTMqwtZeiDdebq3k6Wt7ieYvpXTg0qvgsjQIovRZWaBDBJy9x8C2hW+w9lMQjF
HkJ7Jy/PHCJ69EzebQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"eu-central-2"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC2mdGdps5Rz2jzYcGNsgETTGUt
hJRrVqSnUWJXTlVaIbkGPLKO6Or7AfWKFp2sgRJ8vLsjoBVR5cESVK7cuK1wItjv
Jyi/opKZAUusJx2hpgU3pUHhlp9ATh/VeVD582jTd9IY+8t5MDa6Z3fGliByEiXz
0LEHdi8MBacLREu1TwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"eu-south-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCjiPgW3vsXRj4JoA16WQDyoPc/
eh3QBARaApJEc4nPIGoUolpAXcjFhWplo2O+ivgfCsc4AU9OpYdAPha3spLey/bh
HPRi1JZHRNqScKP0hzsCNmKhfnZTIEQCFvspDRp4zr91/WS06/flJFBYJ6JHhp0K
wM81XQG59lV6kkoW7QIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"eu-south-2"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDB/VvR1+45Aey5zn3vPk6xBm5o
9grSDL6D2iAuprQnfVXn8CIbSDbWFhA3fi5ippjKkh3sl8VyCvCOUXKdOaNrYBrP
RkrdHdBuL2Tc84RO+3m/rxIUZ2IK1fDlC6sWAjddf6sBrV2w2a78H0H8EwuwiSgt
tURBjwJ7KPPJCqaqrQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"eusc-de-east-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDShbskmohorgcjN7gfApw89CEs
g7praCDggaUVXF6gy9JdJ5INcv7+DkwKaAxdNQWDsWZnxIOqPuPfyAYVP3D2/exP
3Rfxj31UomLduu7S83zgiJ8VVL/GzlDELv5/K2rTcd+580NjLSa6FYDcXDip2ir6
TXxRYBYHP65dwniE+wIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"il-central-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDrc24u3AgFxnoPgzxR6yFXOamc
PuxYXhYKWmapb+S8vOy5hpLoRe4RkOrY0cM3bN07GdEMlin5mU0y1t8y3ct4Yewv
mkgT42kTyMM+t1K4S0xsqjXxxS716uGYh7eWtkxrCihj8AbXN/6pa095h+7TZyl2
n83keiNUzM2KoqQVMwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"me-central-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDcaTgW/KyA6zyruJQrYy00a6wq
LA7eeUzk3bMiTkLsTeDQfrkaZMfBAjGaaOymRo1C3qzE4rIenmahvUplu9ZmLwL1
idWXMRX2RlSvIt+d2SeoKOKQWoc2UOFZMHYxDue7zkyk1CIRaBukTeY13/RIrlc6
X61zJ5BBtZXlHwayjQIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"me-south-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC1TQg02RJyHqF9UhGKqupNQVdG
ZaWOctyp6Dq3ggBOfPU8EuDF+fvBc3egJgAproHgT0JOx5um9voP3eLVsnMsS2kJ
XKvNQwdRpl3oUZGUYBnkYHtXj1ndU1ANCPD/0AU+0oFHj5v5ADUhVPj+7O7tTTZf
r8p8otMFFK8O/8CGUwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
    (
        frozenset({"mx-central-1"}),
        b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDfvOnCzm1iN58Nm7k6ehoy6v0l
nnFI617D6CY3bfuq01RCdEQL96+pYawJieTH8JAQKj02CAa3AeaqdXTE/pDhI/YK
LreeMb4K68WMn24Wjjs6oxjBbAmsKXtt9ihKHGBFNUhgFrNFYyA2i7ieJviwpHjQ
/XgXiG2u1/t/4VydUwIDAQAB
-----END PUBLIC KEY-----
""",
    ),
)


def _public_key_for_region(region: str) -> rsa.RSAPublicKey:
    for regions, key_pem in _PUBLIC_KEY_GROUPS:
        if region in regions:
            key = serialization.load_pem_public_key(key_pem)
            if not isinstance(key, rsa.RSAPublicKey):
                raise InvalidAwsInstanceIdentityError
            return key
    raise InvalidAwsInstanceIdentityError


def _required_string(document: dict[str, Any], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidAwsInstanceIdentityError
    return value


def verify_aws_instance_identity(
    document: str,
    signature: str,
) -> AwsInstanceIdentity:
    if not isinstance(document, str) or not 64 <= len(document) <= 4096:
        raise InvalidAwsInstanceIdentityError
    if not isinstance(signature, str) or not 64 <= len(signature) <= 4096:
        raise InvalidAwsInstanceIdentityError
    try:
        parsed = json.loads(document)
    except (TypeError, ValueError) as error:
        raise InvalidAwsInstanceIdentityError from error
    if not isinstance(parsed, dict):
        raise InvalidAwsInstanceIdentityError

    account_id = _required_string(parsed, "accountId")
    instance_id = _required_string(parsed, "instanceId")
    region = _required_string(parsed, "region")
    if (
        _ACCOUNT_ID.fullmatch(account_id) is None
        or _INSTANCE_ID.fullmatch(instance_id) is None
        or _REGION.fullmatch(region) is None
    ):
        raise InvalidAwsInstanceIdentityError

    try:
        signature_bytes = base64.b64decode(signature, validate=True)
        _public_key_for_region(region).verify(
            signature_bytes,
            document.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (
        binascii.Error,
        InvalidSignature,
        TypeError,
        ValueError,
    ) as error:
        raise InvalidAwsInstanceIdentityError from error

    return AwsInstanceIdentity(
        account_id=account_id,
        instance_id=instance_id,
        region=region,
    )
