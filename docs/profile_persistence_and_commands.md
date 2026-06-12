# 사주 프로필 저장 및 명령어 운영 지침

## 1. 최초 저장

사용자는 최초 1회만 생년월일을 저장한다.

```text
봇 생년월일 1987-12-28 08:30 여
```

저장 후에는 같은 사용자 ID로 들어오는 요청에서 아래 명령을 바로 사용할 수 있다.

```text
봇 프로필확인
봇 사주 올해 돈운
봇 사주 직장운
봇 관상 이마가 넓고 눈매가 또렷함, 일운
봇 손금 생명선 길고 두뇌선이 아래로 휘어짐, 재물운
```

## 2. 현재 구현된 저장 방식

프로필은 JSON 파일에 저장된다.

기본 경로는 다음 환경변수로 바꿀 수 있다.

```bash
BOT_PROFILE_PATH=/persistent/data/bot_profiles.json
```

`BOT_PROFILE_PATH`를 지정하지 않으면 코드별 기본값을 사용한다.

- `bot_services.py`, `bot_services_private.py`: `data/bot_profiles.json`
- `messenger_api.py`: `/tmp/bot_profiles.json`

## 3. Render 무료 인스턴스 주의

Render 무료 인스턴스의 로컬 파일은 재시작이나 재배포 후 사라질 수 있다. 이 경우 코드는 정상이어도 사용자가 다시 생년월일을 입력해야 한다.

영구 저장이 필요하면 다음 중 하나가 필요하다.

1. Render Disk 같은 영구 디스크 연결
2. 외부 DB 연결
3. GitHub/Google Sheet 같은 외부 저장소에 프로필 저장하도록 별도 구현

현재 레포는 JSON 파일 저장 방식이다.

## 4. 사용자 ID 확인

프로필 재사용은 사용자 ID가 매번 같아야 동작한다.

GET 방식 테스트:

```text
/reply?message=봇%20생년월일%201987-12-28%2008:30%20여&user_id=test-user-1
/reply?message=봇%20프로필확인&user_id=test-user-1
```

두 번째 호출에서 저장 프로필이 보여야 한다.

다른 `user_id`로 호출하면 다른 사용자로 인식한다.

```text
/reply?message=봇%20프로필확인&user_id=other-user
```

## 5. 카카오/메신저 연동 점검

카카오나 메신저R에서 계속 생년월일을 다시 요구하면 실제 요청 payload의 사용자 식별 필드를 확인해야 한다.

서버 로그 또는 요청 본문에서 다음 필드 중 하나가 매번 같은지 확인한다.

```text
userRequest.user.properties.plusfriendUserKey
userRequest.user.properties.appUserId
userRequest.user.properties.botUserKey
userRequest.user.id
user_id
sender
room
```

매번 값이 바뀌거나 비어 있으면 서버가 같은 사람을 구분할 수 없다.

## 6. 관상/손금 명령의 현재 범위

사진 자동 판독은 구현하지 않는다.

대신 사용자가 직접 관찰한 특징을 텍스트로 적으면 저장된 생년월일과 결합해 참고 리딩을 제공한다.

예시:

```text
봇 관상 이마가 넓고 눈매가 또렷함, 요즘 일운
봇 손금 감정선이 진하고 운명선이 약함, 직장운
```

사진만 올리는 기능은 동작하지 않는다.
