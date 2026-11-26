#include <FL/Fl_Input.H>
#include <FL/Fl_Spinner.H>
#include <FL/Fl_Hold_Browser.H>
#include <FL/fl_ask.H>
#include <FL/Fl_Preferences.H>
#include <FL/Fl_Tabs.H>
#include <map>
#include <set>

using EmitAnno=void(*)(int,const char*,const char*,int,const char*,const double*,int,void*);
using EditAnno=int(*)(const char*,int,int,const char*,const double*,int,const char*,const char*,EmitAnno,void*,char*,int);
struct AnnotationRow {
    int kind,layer;
    std::string id,key,value;
    std::vector<Point> ring;
    int parent=-1;
    bool numeric=false;
};
struct Annotator:Viewer {
    static constexpr const char* titles[]={"Point annotation","Polygon annotation","Tag annotation","Word annotation","Display annotation layers"};
    static constexpr const char* types[]={"Point","Polygon","Tag","Word"};
    static constexpr const char* badges[]={"P","G","T","W","Layers"};
    EditAnno callback;
    std::vector<std::unique_ptr<Fl_Button>> tools;
    std::vector<AnnotationRow> rows;
    std::vector<Point> vertices;
    bool shown[4][10]{},stroke=false;
    int address[4][4]{},mode=-1;
    int order[4][10]{},point_action=1,point_radius=7;
    double opacity=.2;
    bool pointer_valid=false,point_circle=false;
    ToolButton save_all{ToolButton::SaveAnnotations,"Save all annotations"};
    inline static Fl_Event_Dispatch previous_dispatch=nullptr;
    inline static unsigned dispatch_users=0;
    static int dispatch(int event,Fl_Window* window){
        // Handle one native keydown before a focused toolbar widget can consume it.
        // Menus, dialogs, and other windows retain normal FLTK keyboard dispatch.
        if(event==FL_KEYDOWN&&!Fl::modal()&&!Fl::grab()){
            if(auto w=dynamic_cast<Window*>(window))if(auto a=dynamic_cast<Annotator*>(w->owner)){
                int key=Fl::event_key();if(key>='A'&&key<='Z')key+='a'-'A';
                if(a->shortcuts(key,Fl::event_state(FL_CTRL|FL_META),Fl::event_state(FL_SHIFT),Fl::event_state(FL_ALT)))return 1;
            }
        }
        return previous_dispatch?previous_dispatch(event,window):Fl::handle_(event,window);
    }
    Point pointer;
    std::array<std::string,10> meanings,words;
    std::array<bool,10> numeric_words{};
    Fl_Box strip{0,0,474,33};
    Fl_Box active_label{0,0,108,29};
    std::array<std::unique_ptr<Fl_Button>,10> swatches;
    Fl_Button palette{0,0,58,27,"Color..."},exit_draw{0,0,44,27,"Close"};
    std::unique_ptr<Fl_Preferences> prefs;
    Annotator(Open o,Read r,Save s,Macro m,EditAnno a,bool remember=false):Viewer(o,r,s),callback(a){
        macro=m;win.label("EasyPath | Slide Annotator");
        if(remember)prefs=std::make_unique<Fl_Preferences>(Fl_Preferences::USER_L,"EasyPath","SlideAnnotator");
        if(prefs){int circle=0;prefs->get("point-circle",circle,0);point_circle=circle!=0;
            prefs->get("point-radius",point_radius,7);point_radius=std::clamp(point_radius,1,20);}
        for(int k=0;k<4;k++){
            char value[11]={};if(prefs)prefs->get(types[k],value,"",11);
            std::string code(value);auto sorted=code;std::sort(sorted.begin(),sorted.end());
            for(int j=0;j<10;j++)order[k][j]=sorted=="0123456789"?code[j]-'0':j;
        }
        for(int i=0;i<5;i++){
            auto b=std::make_unique<ToolButton>((ToolButton::Icon)(ToolButton::PointTool+i),titles[i]);
            b->tooltip(titles[i]);b->box(FL_THIN_UP_BOX);b->color(color(0xf4f6f8));b->selection_color(color(0xc1dcff));b->deactivate();win.add(*b);
            b->callback([](Fl_Widget*w,void*p){auto& a=*(Annotator*)p;
                for(int i=0;i<5;i++)if(a.tools[i].get()==w){if(i==4)a.layers();else a.settings(i);return;}
            },this);tools.push_back(std::move(b));
        }
        for(auto& a:address)std::fill_n(a,4,1);
        for(auto& a:shown)std::fill_n(a,10,true);
        win.add(strip);strip.box(FL_FLAT_BOX);strip.color(color(0xf3f5f7));win.add(active_label);active_label.labelsize(12);
        for(auto& button:swatches){
            button=std::make_unique<Fl_Button>(0,0,24,27);win.add(*button);
            button->callback([](Fl_Widget*w,void*p){auto& a=*(Annotator*)p;
                for(int i=0;i<10;i++)if(w==a.swatches[i].get()){a.select_layer(i);break;}},this);
        }
        win.add(palette);win.add(exit_draw);
        palette.callback([](Fl_Widget*,void*p){auto& a=*(Annotator*)p;a.pick_color(a.mode,a.address[a.mode][3]-1);},this);
        exit_draw.callback([](Fl_Widget*,void*p){((Annotator*)p)->enter_mode(-1);},this);
        exit_draw.tooltip("Close free draw (Esc)");palette.tooltip("Change the selected layer color");
        win.add(save_all);save_all.deactivate();save_all.tooltip("Save all annotations (Ctrl+S)");
        save_all.callback([](Fl_Widget*,void* p){((Annotator*)p)->save_annotations();},this);
        sync();layout();
        win.callback([](Fl_Widget*w,void*p){if(((Annotator*)p)->leave_draft())w->hide();},this);
        if(!dispatch_users++){previous_dispatch=Fl::event_dispatch();Fl::event_dispatch(dispatch);}
    }
    ~Annotator() override{
        win.hide();
        if(!--dispatch_users&&Fl::event_dispatch()==dispatch)Fl::event_dispatch(previous_dispatch);
    }
    bool editing() const override{return true;}
    void layout() override{
        Viewer::layout();int x=win.w()/2-82;
        for(int k=0;k<4;k++)tools[k]->position(x+42*k,64);
        tools[4]->position(win.w()-178,64);
        save_all.position(win.w()-220,64);
        x=win.w()/2-221;strip.resize(x-5,104,452,35);active_label.position(x,107);
        for(int i=0;i<10;i++)swatches[i]->position(x+108+i*23,108);
        palette.position(x+340,108);exit_draw.position(x+400,108);
    }
    void sync(){
        auto visible=[&](Fl_Widget& w){if(mode<0)w.hide();else w.show();};
        visible(strip);visible(active_label);visible(palette);visible(exit_draw);
        if(mode>=0)active_label.copy_label((std::string(types[mode])+" free draw").c_str());
        for(int i=0;i<10;i++){
            auto& b=*swatches[i];visible(b);if(mode<0)continue;
            int index=order[mode][i];b.color(color(colors[index]));b.labelcolor(fl_contrast(FL_BLACK,b.color()));
            b.box(i+1==address[mode][3]?FL_DOWN_BOX:FL_THIN_UP_BOX);b.labelsize(12);
            b.copy_label(std::to_string(i+1).c_str());b.tooltip(names[index]);
        }
        for(int i=0;i<4;i++)tools[i]->value(mode==i);
        win.redraw();
    }
    void select_layer(int i){
        if(mode<0||i<0||i>=10)return;
        if(mode==1&&!vertices.empty()&&i+1!=address[mode][3]&&!leave_draft())return;
        address[mode][3]=i+1;shown[mode][i]=true;sync();
    }
    void swap_color(int k,int layer,int choice){
        auto& a=order[k];auto other=std::find(a,a+10,choice);if(other==a+10)return;
        std::swap(a[layer],*other);
        if(prefs){std::string code;for(int c:a)code+=char('0'+c);prefs->set(types[k],code.c_str());prefs->flush();}
        sync();
    }
    void pick_color(int k,int i){
        std::array<Fl_Menu_Item,11> menu{};
        for(int j=0;j<10;j++){menu[j].label(names[j]);menu[j].flags=FL_MENU_RADIO;if(order[k][i]==j)menu[j].set();}
        if(auto m=menu[0].popup(Fl::event_x(),Fl::event_y()))swap_color(k,i,int(m-menu.data()));
    }
    bool leave_draft() override {
        if(!vertices.empty()&&fl_choice("Discard the unfinished polygon?","Keep editing","Discard",nullptr)!=1)return false;
        vertices.clear();stroke=false;return true;
    }
    std::string key(int k)const{
        auto& a=address[k];return std::to_string(a[0])+"_"+std::to_string(a[1])+"_"+std::to_string(a[2])+"_"+badges[k]+"_"+std::to_string(a[3]);
    }
    static void collect(int k,const char* id,const char* key,int layer,const char* v,const double* xy,int n,void* dst){
        auto& rs=*(std::vector<AnnotationRow>*)dst;
        int type=k>=8?3:k<4?k:2+(k-4)/2;
        int parent=k>=9?k-9:k>=4&&k<8?(k-4)%2:-1;
        rs.push_back({type,layer,id,key,v,{},parent,k>=8});for(int i=0;i<n;i++)rs.back().ring.push_back({xy[2*i],xy[2*i+1]});
    }
    bool apply(const char* cmd,int k,const std::vector<Point>& pts={},const std::string& val={},const std::string& id={}){
        std::vector<double> xy;for(auto p:pts){xy.push_back(p.x);xy.push_back(p.y);}
        std::vector<AnnotationRow> next;
        if(callback(cmd,k,it().frame,key(k).c_str(),xy.data(),(int)pts.size(),val.c_str(),id.c_str(),collect,&next,msg.data(),msg.size())){
            message(msg.data());return false;
        }
        rows.swap(next);refresh(false);
        if(std::strcmp(cmd,"list"))message("Saved beside "+fs::u8path(it().path).filename().u8string());
        return true;
    }
    void opened() override{
        rows.clear();mode=-1;pointer_valid=false;for(auto& b:tools)b->activate();save_all.activate();sync();
        if(apply("list",0))message(fs::u8path(it().path).filename().u8string()+"  |  Frame "+std::to_string(it().frame+1));
    }
    void tool_mode(int k){
        mode=k;stroke=false;measure.value(0);clear_draft();sync();
        if(k>=0)shown[k][address[k][3]-1]=true;
        canvas.take_focus();
        message(k==0?"Point free draw":k==1?"Polygon free draw: hover over the slide and press Shift+Z to trace.":k==2?"Tag free draw":k==3?"Word free draw":"Pan");refresh(false);
    }
    bool enter_mode(int k){if(k==mode)return true;if(!leave_draft())return false;tool_mode(k);return true;}
    void settings(int k){
        if(active<0)return;
        stroke=false;
        Fl_Window w(534,335,titles[k]);Fl_Tabs tabs(14,12,506,252);
        Fl_Group draw(14,41,506,223,"Free draw");
        Fl_Spinner a(118,57,76,30,"Primary"),b(379,57,76,30,"Secondary"),c(118,98,76,30,"Tertiary"),d(379,98,76,30,"Layer");
        std::array<Fl_Spinner*,4> boxes{&a,&b,&c,&d};
        for(int j=0;j<4;j++){boxes[j]->range(1,j==3?10:20);boxes[j]->step(1);boxes[j]->value(address[k][j]);}
        Fl_Check_Button on(38,149,208,29,"Free draw");on.value(mode==k);
        Fl_Choice action(303,149,155,28,"Action");action.add("None|Add points|Remove points");action.value(point_action);
        Fl_Choice format(303,149,155,28,"Format");format.add("Word|Number");format.value(numeric_words[address[3][3]-1]);if(k!=3)format.hide();
        Fl_Input input(118,200,336,29,k==2?"Tag meaning":"Word value");input.maximum_size(500);
        if(k!=0)action.hide();
        if(k<2)input.hide();else input.value((k==2?meanings:words)[address[k][3]-1].c_str());
        struct Change{Annotator* owner;int kind;Fl_Input* text;Fl_Choice* format;std::array<Fl_Spinner*,4> boxes;};
        Change change{this,k,&input,&format,boxes};
        auto load=[](Fl_Widget*,void*p){auto f=(Change*)p;if(f->kind<2)return;
            int layer=int(f->boxes[3]->value());if(f->kind==3)f->format->value(f->owner->numeric_words[layer-1]);
            f->text->value((f->kind==2?f->owner->meanings:f->owner->words)[layer-1].c_str());
            std::string key;for(int j=0;j<3;j++)key+=std::to_string(int(f->boxes[j]->value()))+"_";
            key+=std::string(badges[f->kind])+"_"+std::to_string(layer);
            for(auto& r:f->owner->rows)if(r.kind==f->kind&&r.parent<0&&r.key==key){
                f->text->value(r.value.c_str());if(r.kind==3)f->format->value(r.numeric);break;}};
        for(auto box:boxes)box->callback(load,&change);load(nullptr,&change);
        draw.end();
        Fl_Group display(14,41,506,223,"Display");
        std::array<std::unique_ptr<Fl_Button>,10> chips;
        struct Chip{Annotator* owner;int kind,index;};std::array<Chip,10> choices;
        for(int i=0;i<10;i++){
            auto button=std::make_unique<Fl_Button>(31+95*(i%5),72+52*(i/5),86,36);
            button->copy_label(("Layer "+std::to_string(i+1)).c_str());
            choices[i]={this,k,i};button->color(color(colors[order[k][i]]));button->labelcolor(fl_contrast(FL_BLACK,button->color()));
            button->callback([](Fl_Widget*b,void*p){auto f=(Chip*)p;f->owner->pick_color(f->kind,f->index);
                auto group=b->parent();for(int j=0;j<10;j++){auto w=group->child(j);
                    w->color(color(colors[f->owner->order[f->kind][j]]));w->labelcolor(fl_contrast(FL_BLACK,w->color()));}group->redraw();},&choices[i]);
            chips[i]=std::move(button);
        }
        Fl_Spinner style(173,194,92,27,k==1?"Polygon opacity":"Point radius");
        Fl_Check_Button circle(286,194,170,27,"Change to circle");
        if(k==0)circle.value(point_circle);else circle.hide();
        if(k==0){style.range(1,20);style.value(point_radius);}
        else if(k==1){style.type(FL_FLOAT_INPUT);style.range(0,1);style.step(.1);style.value(opacity);}
        else style.hide();
        display.end();tabs.end();tabs.value(&draw);
        Fl_Button cancel(299,282,97,31,"Cancel"),go(410,282,97,31,"Apply"),slide(25,282,158,31,"Save frame label");
        if(k<2)slide.hide();
        int chosen=0;
        auto finish=[](Fl_Widget*b,void*p){(*(int*)p)=1;b->window()->hide();};
        go.callback(finish,&chosen);
        slide.callback([](Fl_Widget*b,void*p){(*(int*)p)=2;b->window()->hide();},&chosen);
        cancel.callback(close_popup,&w);w.end();modal(w);if(!chosen)return;
        bool changes=false;for(int j=0;j<4;j++)changes|=address[k][j]!=(int)boxes[j]->value();
        if(mode==1&&chosen==1&&((k==1&&(!on.value()||changes))||(k!=1&&on.value()))&&!leave_draft())return;
        for(int j=0;j<4;j++)address[k][j]=(int)boxes[j]->value();
        if(k==0){
            point_action=action.value();point_radius=(int)style.value();point_circle=circle.value()!=0;
            if(prefs){prefs->set("point-radius",point_radius);prefs->set("point-circle",int(point_circle));prefs->flush();}
        }else if(k==1)opacity=style.value();
        if(k>=2)(k==2?meanings:words)[address[k][3]-1]=input.value();
        if(k==3)numeric_words[address[3][3]-1]=format.value()==1;
        if(chosen==2){apply(k==3&&format.value()==1?"add-number":"add",k,{},input.value());return;}
        if(on.value())tool_mode(k);else if(mode==k)tool_mode(-1);else sync();
    }
    void layers(){
        Fl_Window w(602,450,titles[4]);Fl_Hold_Browser list(20,192,562,180);
        std::vector<std::unique_ptr<Fl_Check_Button>> boxes;std::vector<int> indices;std::set<std::pair<int,std::string>> seen;
        for(int k=0;k<4;k++){
            auto label=new Fl_Box(12,12+k*42,64,30,types[k]);label->align(FL_ALIGN_LEFT|FL_ALIGN_INSIDE);
            for(int i=0;i<10;i++){auto b=std::make_unique<Fl_Check_Button>(82+i*50,12+k*42,47,29);
                b->copy_label(std::to_string(i+1).c_str());b->selection_color(color(colors[order[k][i]]));b->value(shown[k][i]);boxes.push_back(std::move(b));}
        }
        list.format_char(0);
        for(int i=0;i<(int)rows.size();i++){auto& r=rows[i];if(r.parent<0&&seen.emplace(r.kind,r.id).second){
            list.add((r.key+"   "+types[r.kind]+"   "+r.value).c_str());indices.push_back(i);}}
        Fl_Button del(20,393,141,32,"Delete selected"),done(490,393,92,32,"Done");bool remove=false;
        del.callback([](Fl_Widget*w,void*p){*(bool*)p=true;w->window()->hide();},&remove);
        done.callback(close_popup,&w);w.end();modal(w);
        for(int k=0;k<4;k++)for(int j=0;j<10;j++)shown[k][j]=boxes[k*10+j]->value();
        if(remove&&list.value()>0)erase(indices.at(list.value()-1));refresh(false);
    }
    void erase(int i){auto r=rows.at(i);if(fl_choice("Remove %s from this frame?","Cancel","Delete",nullptr,r.key.c_str())==1)apply("delete",r.kind,{},"",r.id);}
    void save_annotations(){
        if(active<0||!apply("save",0))return;
        std::string text="All completed annotations saved for "+fs::u8path(it().path).filename().u8string()+" (all frames).";
        if(!vertices.empty())text+=" Draft kept; Shift+S finishes it.";
        message(text);
    }
    void commit(bool clip){
        if(vertices.size()<3){message(vertices.empty()?"Press Shift+Z over the slide to start a polygon.":"Trace at least three points. Shift+Z resumes the draft.");return;}
        if(apply(clip?"clip":"add",1,vertices)){vertices.clear();stroke=false;refresh(false);message(clip?"Polygon clipped and saved.":"Polygon saved.");}
    }
    void push_vertex(Point world){if(!inside(world)||vertices.size()>=100000)return;
        if(vertices.empty()||std::hypot(world.x-vertices.back().x,world.y-vertices.back().y)>=tr().step*1.5){vertices.push_back(world);refresh(false);}}
    int target(Point pos,int kind){
        auto t=tr();
        for(int end=(int)rows.size()-1;end>=0;){
            auto& r=rows[end];int start=end;
            if(r.kind!=kind||r.parent>=0||!shown[kind][r.layer-1]){--end;continue;}
            while(start>0&&rows[start-1].parent<0&&rows[start-1].kind==kind&&rows[start-1].id==r.id)--start;
            bool contained=false;
            for(int n=start;n<=end;n++){
                const auto& ring=rows[n].ring;if(ring.empty())continue;
                auto a=t.screen(ring.back());
                for(auto pt:ring){
                    auto b=t.screen(pt);
                    if(hit_distance(pos,a,b)<=(kind==0?point_radius+2:3))return start;
                    if(kind==1&&((a.y>pos.y)!=(b.y>pos.y))&&pos.x<(b.x-a.x)*(pos.y-a.y)/(b.y-a.y)+a.x)contained=!contained;
                    a=b;
                }
            }
            if(contained)return start;end=start-1;
        }return -1;
    }
    bool object_action(Point p,bool right){
        if(mode<0)return false;
        int i=target(p,mode<2?mode:0);
        if(i<0&&mode>=2)i=target(p,1);
        if(i<0)return false;
        auto r=rows[i];
        if(mode>=2){
            auto& value=(mode==2?meanings:words)[address[mode][3]-1];
            if(right){
                if(fl_choice("Remove all %s labels from this object?","Cancel","Remove",nullptr,mode==2?"tag":"word")!=1)return true;
            }else if(mode==3){
                auto saved=std::find_if(rows.begin(),rows.end(),[&](const auto& label){return label.kind==3&&label.parent==r.kind&&label.id==r.id&&label.key==key(3);});
                bool number=saved!=rows.end()?saved->numeric:numeric_words[address[3][3]-1];
                auto s=fl_input(number?"Number value":"Word value",saved!=rows.end()?saved->value.c_str():value.c_str());
                if(!s||!*s)return true;value=s;numeric_words[address[3][3]-1]=number;
            }
            apply(right?"clear-labels":mode==3&&numeric_words[address[3][3]-1]?"label-number":"label",mode,{},value,std::to_string(r.kind)+":"+r.id);
        }else apply("delete",mode,{},"",r.id);
        return true;
    }
    int edit_event(int e,Point p,Point world) override{
        if(e==FL_LEAVE){pointer_valid=false;return 0;}
        if(e==FL_MOVE||e==FL_DRAG||e==FL_PUSH||e==FL_ENTER){pointer=p;pointer_valid=inside(world);}
        if(measure.value()||mode<0||Fl::event_state(FL_ALT))return 0;
        if(e==FL_PUSH&&Fl::event_button()==FL_RIGHT_MOUSE){stroke=false;
            if(!Fl::event_state(FL_CTRL|FL_META))object_action(p,true);return 1;}
        if(mode==1){
            if(e==FL_MOVE&&stroke){push_vertex(world);return 1;}
            if(e==FL_PUSH)stroke=false;
            return 0; // Drag to pan without appending points to a paused draft.
        }
        if(e==FL_PUSH&&Fl::event_button()==FL_LEFT_MOUSE&&inside(world)&&!pixels.empty()){
            if(mode==0&&point_action==1){apply("add",0,{world});return 1;}
            if(mode==3&&!Fl::event_clicks())return 0;
            if(mode>=2||point_action==2)return object_action(p,false)?1:0;
        }
        return 0;
    }
    int shortcuts(int k,bool ctrl,bool shift,bool alt){
        if(active<0)return 0;
        if(ctrl&&!alt&&!shift&&k=='s'){stroke=false;save_annotations();return 1;}
        if(angle.contains(Fl::focus())||Fl::focus()==&angle.input)return 0;
        if(k==FL_Shift_L||k==FL_Shift_R)return 0;
        if(!(shift&&!ctrl&&k=='z'))stroke=false;
        if(ctrl){
            if(!shift&&!alt&&k=='p')enter_mode(0);
            else if(!shift&&!alt&&k=='g')enter_mode(1);
            else if(!shift&&k=='t')enter_mode(2);
            else if(!shift&&!alt&&k=='o')enter_mode(3);
            else if(!alt&&(k=='z'||k=='y')){if(vertices.empty())apply(k=='y'||shift?"redo":"undo",0);else message("Finish or clear the polygon first.");}
            else if(!alt&&!shift&&(k=='q'||k=='w'||k=='e')){opacity=k=='q'?0:k=='w'?.2:.6;refresh(false);}
            else if(!alt&&!shift&&k>='0'&&k<='9'&&mode==2)select_layer((k-'0'+9)%10);
            else return 0;return 1;
        }
        if(alt)return 0;
        if(k==FL_Escape&&mode>=0){enter_mode(-1);return 1;}
        if((mode==0&&!shift||mode==1&&shift)&&k>='0'&&k<='9'){select_layer((k-'0'+9)%10);return 1;}
        if(mode==0&&!shift&&(k=='q'||k=='w'||k=='e')){
            point_action=k=='q'?0:k=='w'?1:2;message(point_action==0?"Point: pan":point_action==1?"Add points":"Remove points");return 1;
        }
        if(shift&&mode==1){
            if(k=='z'){if(pointer_valid&&inside(tr().world(pointer))&&!pixels.empty()){stroke=true;push_vertex(tr().world(pointer));message("Recording polygon. Shift+S saves; Shift+D clips.");}
                else message("Move the pointer over the slide, then press Shift+Z.");}
            else if(k=='s'||k=='d')commit(k=='d');
            else if(k=='r'){vertices.clear();refresh(false);message("Polygon draft cleared.");}
            else if(k=='a'){vertices.resize(vertices.size()>10?vertices.size()-10:0);refresh(false);message("Stepped back. Shift+Z resumes the draft.");}
            else return 0;return 1;
        }
        return 0;
    }
    void filled_polygons(Point origin){
        if(opacity<=0)return;
        RGB raster;auto t=tr();struct Edge{int end;double x,dx;};
        for(size_t i=0;i<rows.size();){
            auto& first=rows[i];
            if(first.kind!=1||first.parent>=0||!shown[1][first.layer-1]){++i;continue;}
            size_t end=i+1;while(end<rows.size()&&rows[end].kind==1&&rows[end].parent<0&&rows[end].id==first.id)++end;
            std::map<int,std::vector<Edge>> starts;
            for(size_t f=i;f<end;f++){
                auto& ring=rows[f].ring;if(ring.empty())continue;
                Point prev=t.screen(ring.back());
                for(auto pt:ring){
                    auto cur=t.screen(pt),a=prev,b=cur;prev=cur;if(a.y>b.y)std::swap(a,b);
                    int lo=int(std::clamp(std::ceil(a.y-.5),0.,double(ph))),hi=int(std::clamp(std::ceil(b.y-.5),0.,double(ph)));
                    if(lo>=hi)continue;double dx=(b.x-a.x)/(b.y-a.y);
                    starts[lo].push_back({hi,a.x+(lo+.5-a.y)*dx,dx});
                }
            }
            std::vector<Edge> active;auto ink=colors[order[1][first.layer-1]];
            unsigned char col[]={(unsigned char)(ink>>16),(unsigned char)(ink>>8),(unsigned char)ink};
            int alpha=int(std::round(opacity*255));
            for(auto it=starts.begin();it!=starts.end()||!active.empty();){
                int y=it->first;
                for(;y<ph;y++){
                    active.erase(std::remove_if(active.begin(),active.end(),[&](Edge e){return e.end<=y;}),active.end());
                    if(it!=starts.end()&&it->first==y){active.insert(active.end(),it->second.begin(),it->second.end());++it;}
                    if(active.empty())break;
                    std::sort(active.begin(),active.end(),[](Edge a,Edge b){return a.x<b.x;});
                    for(size_t h=1;h<active.size();h+=2){
                        int x0=int(std::clamp(std::ceil(active[h-1].x-.5),0.,double(pw)));
                        int x1=int(std::clamp(std::ceil(active[h].x-.5),0.,double(pw)));
                        if(x0>=x1)continue;if(raster.empty())raster=pixels;
                        auto dst=raster.data()+(size_t(y)*pw+x0)*3;
                        for(int x=x0;x<x1;x++)for(int c=0;c<3;c++,dst++)*dst=(col[c]*alpha+*dst*(255-alpha)+127)/255;
                    }
                    for(auto& e:active)e.x+=e.dx;
                }
                if(y==ph)break;
            }
            i=end;
        }
        if(!raster.empty())fl_draw_image(raster.data(),int(origin.x),int(origin.y),pw,ph,3);
    }
    void paint_annotations(Point origin,bool live) override{
        int y=live?int(origin.y)+108:22;auto t=tr();filled_polygons(origin);
        std::set<std::pair<int,std::string>> visible;
        std::map<std::pair<int,std::string>,int> lines;
        auto outline=[&](const std::vector<Point>& pts){for(size_t i=1;i<pts.size();i++)line(t.screen(pts[i-1])+origin,t.screen(pts[i])+origin);};
        for(auto& r:rows){if(!shown[r.kind][r.layer-1])continue;
            auto col=color(colors[order[r.kind][r.layer-1]]);
            if(r.kind<2)visible.emplace(r.kind,r.id);
            if(r.kind>=2){
                Point at{origin.x+14,double(y)};
                if(r.parent>=0){
                    auto id=std::make_pair(r.parent,r.id);if(!visible.count(id))continue;
                    at=t.screen(r.ring.front())+origin+Point{10.,-6.+21.*lines[id]++};
                }else{if(y>=origin.y+ph-40)continue;y+=23;}
                if(at.x>origin.x+pw||at.y<origin.y||at.y>origin.y+ph)continue;
                auto label=r.key+"  "+r.value;fl_font(FL_HELVETICA_BOLD,12);
                fl_color(col);fl_rectf(int(at.x)-3,int(at.y)-13,int(fl_width(label.c_str()))+6,17);
                fl_color(fl_contrast(FL_BLACK,col));fl_draw(label.c_str(),int(at.x),int(at.y));continue;
            }
            fl_color(col);fl_line_style(FL_SOLID,2);
            if(r.kind==0){auto p=t.screen(r.ring.at(0))+origin;
                int x=int(p.x)-point_radius,y=int(p.y)-point_radius,d=2*point_radius+1;
                if(point_circle){fl_line_style(FL_SOLID,point_radius<3?1:2);fl_arc(x,y,d,d,0,360);}
                else fl_pie(x,y,d,d,0,360);}
            else outline(r.ring);
        }
        if(live&&!vertices.empty()){fl_color(color(colors[order[1][address[1][3]-1]]));fl_line_style(FL_DASH,2);outline(vertices);
            auto p=t.screen(vertices.front())+origin;line(t.screen(vertices.back())+origin,p);fl_rect(int(p.x)-2,int(p.y)-2,5,5);}
        fl_line_style(0);
    }
};
API int annotator_show(Open o,Read r,Save s,Macro m,EditAnno a,int smoke){
    Fl::scheme("gtk+");Annotator v(o,r,s,m,a,!smoke);v.win.show();int result=0;if(smoke)Fl::check();else result=Fl::run();v.win.hide();return result;
}
API int annotator_check(Open o,Read r,Save s,Macro m,EditAnno a,const char* scan,const char* stack,const char* png){
    Fl::scheme("gtk+");Annotator v(o,r,s,m,a);v.append(scan);v.append(stack);v.win.show();Fl::check();
    if(!v.select(1)||v.count!=3||!v.render()||!v.rows.empty())return 1;
    if(!v.apply("add",0,{{10.25,30.5}})||!v.apply("add",1,{{2,2},{90,2},{90,65},{2,65}}))return 2;
    v.frame(1);if(!v.rows.empty()||!v.apply("add",2,{},"Test tag")||!v.apply("add",3,{},"Test word"))return 3;
    if(!v.select(0)||!v.rows.empty()||!v.select(1)||v.rows.size()!=2)return 4;
    v.frame(0);v.rotate(37);auto clean=v.compose(false,false);auto ink=v.compose(false,false,true);
    if(v.rows.size()!=2||ink.empty()||ink==clean||s(png,v.pw,v.ph,ink.data(),v.msg.data(),v.msg.size()))return 5;
    v.shown[0][0]=v.shown[1][0]=false;
    if(v.compose(false,false,true)!=v.compose(false,false))return 6;
    v.frame(2);if(!v.render())return 7;
    auto key=[&](int k,bool ctrl=false,bool shift=false,bool alt=false){return v.shortcuts(k,ctrl,shift,alt);};
    if(!key('p',true)||v.mode!=0||!v.strip.visible()||!key('0')||v.address[0][3]!=10)return 8;
    if(!key('q')||v.point_action!=0||!key('e')||v.point_action!=2||!key('w')||v.point_action!=1)return 9;
    if(!key('g',true)||v.mode!=1||!key('3',false,true)||v.address[1][3]!=3)return 10;
    v.pointer=v.tr().screen({5,5});v.pointer_valid=true;
    if(!key('z',false,true)||!v.stroke||v.vertices.size()!=1)return 11;
    auto move=[&](Point q){v.edit_event(FL_MOVE,v.tr().screen(q),q);};
    move({125,5});key('x');move({125,80});
    if(v.stroke||v.vertices.size()!=2||!key('z',false,true))return 12;
    move({5,80});if(!key('s',false,true)||!v.vertices.empty()||v.rows.size()!=1)return 13;
    auto hole=std::vector<Point>{{25,25},{65,25},{65,65},{25,65}};
    v.vertices=hole;if(!key('d',false,true)||!v.vertices.empty()||v.rows.size()!=2)return 14;
    if(v.target(v.tr().screen({40,40}),1)>=0||v.target(v.tr().screen({110,45}),1)<0)return 15;
    auto prior=v.compose(false,false,true);v.swap_color(1,2,3);
    if(v.order[1][2]!=3||v.order[1][3]!=2||v.order[0][2]!=2||prior==v.compose(false,false,true))return 16;
    v.shown[1][2]=false;if(v.target(v.tr().screen({110,45}),1)>=0)return 17;
    v.shown[1][2]=true;key('t',true,false,true);key('2',true);v.meanings[1]="Review";
    if(!v.object_action(v.tr().screen({110,45}),false)||v.rows.back().parent!=1||v.rows.back().value!="Review")return 18;
    key('o',true);if(v.mode!=3||!key('s',true)||!key(FL_Escape)||v.mode!=-1||v.strip.visible())return 19;
    if(!v.select(0)||!v.select(1)||v.it().frame!=2||v.rows.back().value!="Review")return 20;
    std::string id=v.rows[0].id;
    if(!v.apply("label-number",3,{},"1.25","1:"+id)||!v.apply("add-number",3,{},"2.5"))return 21;
    if(!v.select(0)||!v.select(1))return 22;
    int numeric=0;for(auto& r:v.rows)if(r.kind==3&&r.numeric&&((r.parent==1&&r.value=="1.25")||(r.parent==-1&&r.value=="2.5")))numeric++;
    if(numeric!=2)return 23;
    key('g',true);
    if(v.object_action(v.tr().screen({40,40}),true)||!v.object_action(v.tr().screen({110,45}),true))return 24;
    if(v.rows.size()!=1||v.rows[0].parent!=-1||!v.rows[0].numeric||!key('z',true)||v.rows.size()!=5)return 25;
    if(!key('8',false,true)||v.address[1][3]!=8)return 26;
    const auto before_cross=v.rows.size();
    v.pointer=v.tr().screen({10,10});v.pointer_valid=true;
    if(!key('z',false,true)||!v.stroke)return 27;
    move({90,90});move({10,90});move({90,10});
    const auto crossed=v.vertices;
    if(!key('s',false,true)||v.stroke||!v.vertices.empty()||v.rows.size()!=before_cross+2)return 28;
    v.vertices=crossed;
    if(!key('d',false,true)||!v.vertices.empty()||v.rows.size()!=before_cross)return 29;
    v.win.hide();return 0;
}
